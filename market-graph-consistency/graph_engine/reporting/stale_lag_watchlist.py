from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode, RelationshipSource, RelationshipType
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


BANNER = (
    "Saved-file-only stale/lag watchlist. WATCH rows require deterministic timestamp skew, "
    "probability difference, and relationship evidence."
)
WHY_REVIEW_ONLY_YET = (
    "Saved graph diagnostic only; timestamp freshness, relationship evidence, source alignment, and downstream "
    "review must be verified outside this report."
)
DEFAULT_STALE_SECONDS = 30 * 60
DEFAULT_RELATED_FRESH_SECONDS = 5 * 60
DEFAULT_PROBABILITY_DELTA = 0.10
UNIFORM_TIMESTAMP_SKEW_SECONDS = 60
UNIFORM_TIMESTAMP_BLOCKER = "uniform_fixture_or_snapshot_timestamps_no_skew_detectable"

# Freshness buckets are diagnostic-only labels operators use to triage rows at a
# glance. They are assigned per row from the *worst* (oldest) quote age in the
# pair — the bucket name describes the noisiest input, not the implied trading
# state. ``uniform_timestamps_suspicious`` overrides any age-based bucket so
# fixture/snapshot batches with identical timestamps cannot masquerade as fresh
# or stale data.
FRESHNESS_BUCKET_FRESH = "fresh"
FRESHNESS_BUCKET_MAYBE_STALE = "maybe_stale"
FRESHNESS_BUCKET_STALE = "stale"
FRESHNESS_BUCKET_MISSING_TIMESTAMP = "missing_timestamp"
FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS = "uniform_timestamps_suspicious"
FRESHNESS_BUCKETS = (
    FRESHNESS_BUCKET_FRESH,
    FRESHNESS_BUCKET_MAYBE_STALE,
    FRESHNESS_BUCKET_STALE,
    FRESHNESS_BUCKET_MISSING_TIMESTAMP,
    FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS,
)
DETERMINISTIC_EDGE_RELATIONS = {
    RelationshipType.IMPLICATION,
    RelationshipType.MUTUAL_EXCLUSION,
    RelationshipType.COMPLEMENT,
    RelationshipType.SUBSET,
    RelationshipType.SUPERSET,
    RelationshipType.SAME_EVENT_REWORDED,
}
DETERMINISTIC_EDGE_SOURCES = {
    RelationshipSource.MANUAL,
    RelationshipSource.FIXTURE,
    RelationshipSource.HEURISTIC,
    RelationshipSource.MIXED,
}


def build_stale_lag_watchlist_report(
    snapshot: GraphSnapshot,
    *,
    llm_hypotheses_report: dict[str, Any] | None = None,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    related_fresh_seconds: int = DEFAULT_RELATED_FRESH_SECONDS,
    probability_delta_threshold: float = DEFAULT_PROBABILITY_DELTA,
) -> dict[str, Any]:
    llm_pairs = _llm_stale_lag_pairs(llm_hypotheses_report)
    rows: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for left_id, right_id in sorted(_candidate_pairs(snapshot, llm_pairs)):
        seen_pairs.add(tuple(sorted([left_id, right_id])))
        rows.append(
            _build_pair_row(
                snapshot,
                left_id,
                right_id,
                llm_pairs=llm_pairs,
                stale_seconds=stale_seconds,
                related_fresh_seconds=related_fresh_seconds,
                probability_delta_threshold=probability_delta_threshold,
            )
        )
    for pair in sorted(llm_pairs - seen_pairs):
        rows.append(
            _llm_only_row(
                snapshot,
                pair,
                stale_seconds=stale_seconds,
                related_fresh_seconds=related_fresh_seconds,
            )
        )
    rows = sorted(
        rows,
        key=lambda row: (
            row["deterministic_lag_evidence"] is not True,
            -float(row["probability_delta"] or 0.0),
            row["markets_involved"],
        ),
    )
    for rank, row in enumerate(rows, start=1):
        row["diagnostic_rank"] = rank
    watch_count = sum(1 for row in rows if row["deterministic_lag_evidence"] is True)
    blocked_count = sum(1 for row in rows if row["deterministic_lag_evidence"] is not True)
    uniform_timestamps_blocked_count = sum(1 for row in rows if UNIFORM_TIMESTAMP_BLOCKER in row["blockers"])
    freshness_buckets = {bucket: 0 for bucket in FRESHNESS_BUCKETS}
    for row in rows:
        freshness_buckets[row["freshness_bucket"]] += 1
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "stale_seconds": stale_seconds,
        "related_fresh_seconds": related_fresh_seconds,
        "probability_delta_threshold": round(probability_delta_threshold, 6),
        "stale_lag_watch_count": watch_count,
        "stale_lag_blocked_count": blocked_count,
        "uniform_timestamps_blocked_count": uniform_timestamps_blocked_count,
        "freshness_buckets": freshness_buckets,
        "llm_stale_lag_cowitness_count": sum(1 for row in rows if row["llm_stale_lag_cowitness"] is True),
        "stale_lag_watchlist": rows,
    }
    validate_stale_lag_watchlist_report(report)
    return report


def write_stale_lag_watchlist_report(
    snapshot: GraphSnapshot,
    json_output: Path | str,
    markdown_output: Path | str,
    *,
    llm_hypotheses_report: dict[str, Any] | None = None,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    related_fresh_seconds: int = DEFAULT_RELATED_FRESH_SECONDS,
    probability_delta_threshold: float = DEFAULT_PROBABILITY_DELTA,
) -> dict[str, Any]:
    report = build_stale_lag_watchlist_report(
        snapshot,
        llm_hypotheses_report=llm_hypotheses_report,
        stale_seconds=stale_seconds,
        related_fresh_seconds=related_fresh_seconds,
        probability_delta_threshold=probability_delta_threshold,
    )
    markdown = render_stale_lag_watchlist_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError("stale/lag watchlist Markdown contains prohibited vocabulary: " + ", ".join(findings))
    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def validate_stale_lag_watchlist_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("stale/lag watchlist must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("stale/lag watchlist must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("stale/lag watchlist actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("stale_lag_watchlist")
    if not isinstance(rows, list):
        raise SchemaValidationError("stale_lag_watchlist must be a list")
    if report.get("stale_lag_watch_count") != sum(1 for row in rows if row.get("deterministic_lag_evidence") is True):
        raise SchemaValidationError("stale_lag_watch_count must match deterministic rows")
    if report.get("stale_lag_blocked_count") != sum(1 for row in rows if row.get("deterministic_lag_evidence") is not True):
        raise SchemaValidationError("stale_lag_blocked_count must match blocked rows")
    if not isinstance(report.get("uniform_timestamps_blocked_count"), int) or isinstance(
        report.get("uniform_timestamps_blocked_count"), bool
    ):
        raise SchemaValidationError("uniform_timestamps_blocked_count must be an integer")
    if report.get("uniform_timestamps_blocked_count") != sum(
        1 for row in rows if UNIFORM_TIMESTAMP_BLOCKER in row.get("blockers", [])
    ):
        raise SchemaValidationError("uniform_timestamps_blocked_count must match uniform timestamp blocked rows")
    freshness_buckets = report.get("freshness_buckets")
    if not isinstance(freshness_buckets, dict):
        raise SchemaValidationError("freshness_buckets must be an object")
    if set(freshness_buckets.keys()) != set(FRESHNESS_BUCKETS):
        raise SchemaValidationError("freshness_buckets must list every supported bucket exactly once")
    for bucket, count in freshness_buckets.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise SchemaValidationError(f"freshness_buckets[{bucket!r}] must be a non-negative integer")
        if count != sum(1 for row in rows if row.get("freshness_bucket") == bucket):
            raise SchemaValidationError(f"freshness_buckets[{bucket!r}] must match per-row bucket counts")
    for index, row in enumerate(rows):
        _validate_row(row, f"stale_lag_watchlist[{index}]")


def render_stale_lag_watchlist_markdown(report: dict[str, Any]) -> str:
    buckets = report.get("freshness_buckets") or {bucket: 0 for bucket in FRESHNESS_BUCKETS}
    lines = [
        "# Market Graph Stale Lag Watchlist",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Watch rows: `{report['stale_lag_watch_count']}`",
        f"- Blocked rows: `{report['stale_lag_blocked_count']}`",
        f"- Uniform-timestamp blocked rows: `{report['uniform_timestamps_blocked_count']}`",
        "",
        "## Freshness buckets",
        "",
    ]
    for bucket in FRESHNESS_BUCKETS:
        lines.append(f"- `{bucket}`: {int(buckets.get(bucket, 0))}")
    lines.extend(
        [
            "",
            "| Markets | Venues | Stale age (s) | Related age (s) | Probability delta | Freshness | Deterministic? | LLM co-witness? | Blockers |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if not report["stale_lag_watchlist"]:
        lines.append("| none |  |  |  |  |  |  |  |  |")
    for row in report["stale_lag_watchlist"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(", ".join(row["markets_involved"])),
                    _md(", ".join(row["venues_involved"])),
                    _md(row["quote_age_seconds"]),
                    _md(row["related_market_quote_age_seconds"]),
                    _md(row["probability_delta"]),
                    _md(row["freshness_bucket"]),
                    _yes_no(row["deterministic_lag_evidence"]),
                    _yes_no(row["llm_stale_lag_cowitness"]),
                    _md(", ".join(row["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _yes_no(value: Any) -> str:
    return "yes" if value is True else "no"


def _candidate_pairs(snapshot: GraphSnapshot, llm_pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for edge in snapshot.edges:
        pairs.add(tuple(sorted([edge.src_market_id, edge.dst_market_id])))
    families: dict[str, list[str]] = {}
    for node in snapshot.nodes.values():
        family = _fixture_family(node)
        if family:
            families.setdefault(family, []).append(node.market_id)
    for market_ids in families.values():
        for left_index, left_id in enumerate(sorted(market_ids)):
            for right_id in sorted(market_ids)[left_index + 1 :]:
                pairs.add((left_id, right_id))
    for node in snapshot.nodes.values():
        related_id = _declared_related_market_id(node)
        if related_id:
            pairs.add(tuple(sorted([node.market_id, related_id])))
    pairs |= llm_pairs
    return pairs


def _build_pair_row(
    snapshot: GraphSnapshot,
    left_id: str,
    right_id: str,
    *,
    llm_pairs: set[tuple[str, str]],
    stale_seconds: int,
    related_fresh_seconds: int,
    probability_delta_threshold: float,
) -> dict[str, Any]:
    pair = tuple(sorted([left_id, right_id]))
    left = snapshot.nodes.get(left_id)
    right = snapshot.nodes.get(right_id)
    if left is None or right is None:
        return _missing_related_row(
            snapshot,
            pair,
            llm_pairs,
            stale_seconds=stale_seconds,
            related_fresh_seconds=related_fresh_seconds,
        )

    evidence, evidence_blockers = _deterministic_relationship_evidence(snapshot, left, right)
    left_age = _quote_age_seconds(snapshot, left)
    right_age = _quote_age_seconds(snapshot, right)
    left_probability, left_probability_blockers = _node_probability(left)
    right_probability, right_probability_blockers = _node_probability(right)
    blockers = set(evidence_blockers + left_probability_blockers + right_probability_blockers)
    stale_node, related_node = left, right
    quote_age_seconds = left_age
    related_age_seconds = right_age
    if left_age is not None and right_age is not None:
        if abs(left_age - right_age) <= UNIFORM_TIMESTAMP_SKEW_SECONDS:
            blockers.add(UNIFORM_TIMESTAMP_BLOCKER)
        left_stale = left_age > stale_seconds and right_age < related_fresh_seconds
        right_stale = right_age > stale_seconds and left_age < related_fresh_seconds
        if right_stale and not left_stale:
            stale_node, related_node = right, left
            quote_age_seconds = right_age
            related_age_seconds = left_age
        elif not left_stale and not right_stale:
            blockers.add("timestamp_skew_below_threshold")
    else:
        blockers.add("missing_quote_timestamp")
    if quote_age_seconds is None:
        blockers.add("missing_quote_timestamp")
    if related_age_seconds is None:
        blockers.add("missing_related_market_quote_timestamp")

    probability_delta = None
    if left_probability is None or right_probability is None:
        blockers.add("missing_probability_input")
    else:
        probability_delta = round(abs(left_probability - right_probability), 6)
        if probability_delta < probability_delta_threshold:
            blockers.add("probability_delta_below_threshold")
    if evidence is None:
        blockers.add("missing_deterministic_relationship_evidence")

    deterministic = (
        quote_age_seconds is not None
        and quote_age_seconds > stale_seconds
        and related_age_seconds is not None
        and related_age_seconds < related_fresh_seconds
        and probability_delta is not None
        and probability_delta >= probability_delta_threshold
        and evidence is not None
        and not blockers
    )
    if _uses_midpoint_or_synthetic(left) or _uses_midpoint_or_synthetic(right):
        deterministic = False
        blockers.add("non_actionable_probability_input")
    return _row(
        row_id=f"stale_lag:{pair[0]}:{pair[1]}",
        markets=[stale_node.market_id, related_node.market_id],
        venues=sorted({left.venue, right.venue}),
        quote_age_seconds=quote_age_seconds,
        related_age_seconds=related_age_seconds,
        probability_delta=probability_delta,
        deterministic=deterministic,
        llm_cowitness=pair in llm_pairs,
        blockers=sorted(blockers),
        stale_seconds=stale_seconds,
        related_fresh_seconds=related_fresh_seconds,
    )


def _missing_related_row(
    snapshot: GraphSnapshot,
    pair: tuple[str, str],
    llm_pairs: set[tuple[str, str]],
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    related_fresh_seconds: int = DEFAULT_RELATED_FRESH_SECONDS,
) -> dict[str, Any]:
    existing = [market_id for market_id in pair if market_id in snapshot.nodes]
    venues = sorted({snapshot.nodes[market_id].venue for market_id in existing})
    return _row(
        row_id=f"stale_lag:{pair[0]}:{pair[1]}",
        markets=list(pair),
        venues=venues,
        quote_age_seconds=None,
        related_age_seconds=None,
        probability_delta=None,
        deterministic=False,
        llm_cowitness=pair in llm_pairs,
        blockers=["missing_related_market"],
        stale_seconds=stale_seconds,
        related_fresh_seconds=related_fresh_seconds,
    )


def _llm_only_row(
    snapshot: GraphSnapshot,
    pair: tuple[str, str],
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    related_fresh_seconds: int = DEFAULT_RELATED_FRESH_SECONDS,
) -> dict[str, Any]:
    existing = [market_id for market_id in pair if market_id in snapshot.nodes]
    venues = sorted({snapshot.nodes[market_id].venue for market_id in existing})
    return _row(
        row_id=f"stale_lag:{pair[0]}:{pair[1]}",
        markets=list(pair),
        venues=venues,
        quote_age_seconds=None,
        related_age_seconds=None,
        probability_delta=None,
        deterministic=False,
        llm_cowitness=True,
        blockers=["llm_only_not_deterministic_evidence"],
        stale_seconds=stale_seconds,
        related_fresh_seconds=related_fresh_seconds,
    )


def _row(
    *,
    row_id: str,
    markets: list[str],
    venues: list[str],
    quote_age_seconds: int | None,
    related_age_seconds: int | None,
    probability_delta: float | None,
    deterministic: bool,
    llm_cowitness: bool,
    blockers: list[str],
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    related_fresh_seconds: int = DEFAULT_RELATED_FRESH_SECONDS,
) -> dict[str, Any]:
    row = {
        "watchlist_id": row_id,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "markets_involved": markets,
        "venues_involved": venues,
        "quote_age_seconds": quote_age_seconds,
        "related_market_quote_age_seconds": related_age_seconds,
        "probability_delta": probability_delta,
        "deterministic_lag_evidence": deterministic,
        "llm_stale_lag_cowitness": llm_cowitness,
        "blockers": blockers,
        "freshness_bucket": _freshness_bucket(
            quote_age_seconds=quote_age_seconds,
            related_age_seconds=related_age_seconds,
            blockers=blockers,
            stale_seconds=stale_seconds,
            related_fresh_seconds=related_fresh_seconds,
        ),
        "why_review_only_yet": WHY_REVIEW_ONLY_YET,
    }
    _validate_row(row, "stale_lag_watchlist[]")
    return row


def _freshness_bucket(
    *,
    quote_age_seconds: int | None,
    related_age_seconds: int | None,
    blockers: list[str],
    stale_seconds: int,
    related_fresh_seconds: int,
) -> str:
    """Diagnose the freshness state of the worst quote in the pair.

    Uniform-timestamp suspicion overrides any age-based bucket because the
    fixture/snapshot collected the pair in the same batch and operators must not
    confuse a freshness label with a real-time freshness signal.
    """

    if UNIFORM_TIMESTAMP_BLOCKER in blockers:
        return FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS
    ages = [age for age in (quote_age_seconds, related_age_seconds) if age is not None]
    if not ages:
        return FRESHNESS_BUCKET_MISSING_TIMESTAMP
    if quote_age_seconds is None or related_age_seconds is None:
        return FRESHNESS_BUCKET_MISSING_TIMESTAMP
    worst_age = max(ages)
    if worst_age > stale_seconds:
        return FRESHNESS_BUCKET_STALE
    if worst_age > related_fresh_seconds:
        return FRESHNESS_BUCKET_MAYBE_STALE
    return FRESHNESS_BUCKET_FRESH


def _deterministic_relationship_evidence(
    snapshot: GraphSnapshot,
    left: MarketNode,
    right: MarketNode,
) -> tuple[str | None, list[str]]:
    pair = {left.market_id, right.market_id}
    for edge in snapshot.edges:
        if {edge.src_market_id, edge.dst_market_id} != pair:
            continue
        if edge.source in DETERMINISTIC_EDGE_SOURCES and edge.relation in DETERMINISTIC_EDGE_RELATIONS:
            return f"edge:{edge.edge_id}", []
        return None, ["non_deterministic_or_unsupported_relationship_edge"]
    left_family = _fixture_family(left)
    right_family = _fixture_family(right)
    if left_family and right_family and left_family == right_family:
        return f"fixture_family:{left_family}", []
    return None, []


def _fixture_family(node: MarketNode) -> str | None:
    for key in [
        "stale_lag_family",
        "event_family",
        "family",
        "event_group_id",
        "group_id",
        "no_arb_family_id",
    ]:
        value = node.raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _declared_related_market_id(node: MarketNode) -> str | None:
    for key in ["stale_lag_related_market_id", "related_market_id"]:
        value = node.raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _quote_age_seconds(snapshot: GraphSnapshot, node: MarketNode) -> int | None:
    if node.raw.get("quote_timestamp_missing") is True:
        return None
    if "quote_age_seconds" in node.raw:
        value = node.raw.get("quote_age_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
        return None
    return max(0, int((snapshot.as_of - node.as_of).total_seconds()))


def _node_probability(node: MarketNode) -> tuple[float | None, list[str]]:
    blockers: list[str] = []
    if _uses_midpoint_or_synthetic(node):
        blockers.append("non_actionable_probability_input")
    if node.yes_price is not None:
        return float(node.yes_price), blockers
    if node.bid is not None and node.ask is not None:
        blockers.append("diagnostic_midpoint_used")
        blockers.append("non_actionable_probability_input")
        return (float(node.bid) + float(node.ask)) / 2.0, blockers
    return None, blockers


def _uses_midpoint_or_synthetic(node: MarketNode) -> bool:
    if node.raw.get("diagnostic_midpoint_used") is True or node.raw.get("non_actionable_input") is True:
        return True
    midpoint = (float(node.bid) + float(node.ask)) / 2.0 if node.bid is not None and node.ask is not None else None
    return node.yes_price is not None and midpoint is not None and abs(float(node.yes_price) - midpoint) <= 1e-9


def _llm_stale_lag_pairs(report: dict[str, Any] | None) -> set[tuple[str, str]]:
    if not isinstance(report, dict):
        return set()
    pairs: set[tuple[str, str]] = set()
    for row in report.get("validated_hypotheses", []):
        if not isinstance(row, dict) or row.get("relationship_type") != "STALE_OR_LAG_HYPOTHESIS":
            continue
        market_ids = [item for item in row.get("source_market_ids", []) if isinstance(item, str)]
        if len(market_ids) < 2:
            continue
        for left_index, left_id in enumerate(sorted(set(market_ids))):
            for right_id in sorted(set(market_ids))[left_index + 1 :]:
                pairs.add((left_id, right_id))
    return pairs


def _validate_row(row: dict[str, Any], path: str) -> None:
    required = [
        "watchlist_id",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "markets_involved",
        "venues_involved",
        "quote_age_seconds",
        "related_market_quote_age_seconds",
        "probability_delta",
        "deterministic_lag_evidence",
        "llm_stale_lag_cowitness",
        "blockers",
        "freshness_bucket",
        "why_review_only_yet",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["freshness_bucket"] not in FRESHNESS_BUCKETS:
        raise SchemaValidationError(f"{path}.freshness_bucket must be one of {FRESHNESS_BUCKETS!r}")
    if row["deterministic_lag_evidence"] is True and row["freshness_bucket"] != FRESHNESS_BUCKET_STALE:
        # A deterministic WATCH row must be in the stale bucket because the
        # deterministic gate only triggers when the worst quote exceeds
        # ``stale_seconds``; any other bucket would silently disagree with
        # the row's headline.
        raise SchemaValidationError(
            f"{path}.deterministic_lag_evidence requires freshness_bucket={FRESHNESS_BUCKET_STALE!r}"
        )
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    for key in ["markets_involved", "venues_involved", "blockers"]:
        if not isinstance(row[key], list) or not all(isinstance(item, str) for item in row[key]):
            raise SchemaValidationError(f"{path}.{key} must be a list of strings")
    if len(row["markets_involved"]) < 2:
        raise SchemaValidationError(f"{path}.markets_involved must contain at least two ids")
    for key in ["quote_age_seconds", "related_market_quote_age_seconds"]:
        value = row[key]
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise SchemaValidationError(f"{path}.{key} must be null or a non-negative integer")
    value = row["probability_delta"]
    if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
        raise SchemaValidationError(f"{path}.probability_delta must be null or non-negative")
    if not isinstance(row["deterministic_lag_evidence"], bool):
        raise SchemaValidationError(f"{path}.deterministic_lag_evidence must be boolean")
    if not isinstance(row["llm_stale_lag_cowitness"], bool):
        raise SchemaValidationError(f"{path}.llm_stale_lag_cowitness must be boolean")
    if row["deterministic_lag_evidence"] is True and row["blockers"]:
        raise SchemaValidationError(f"{path}.deterministic rows must not have blockers")
    if row["deterministic_lag_evidence"] is False and not row["blockers"]:
        raise SchemaValidationError(f"{path}.blocked rows need blockers")
    if not isinstance(row["why_review_only_yet"], str) or not row["why_review_only_yet"]:
        raise SchemaValidationError(f"{path}.why_review_only_yet must be non-empty")
    _reject_prohibited_tokens(row)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "DEFAULT_PROBABILITY_DELTA",
    "DEFAULT_RELATED_FRESH_SECONDS",
    "DEFAULT_STALE_SECONDS",
    "FRESHNESS_BUCKETS",
    "FRESHNESS_BUCKET_FRESH",
    "FRESHNESS_BUCKET_MAYBE_STALE",
    "FRESHNESS_BUCKET_MISSING_TIMESTAMP",
    "FRESHNESS_BUCKET_STALE",
    "FRESHNESS_BUCKET_UNIFORM_SUSPICIOUS",
    "UNIFORM_TIMESTAMP_BLOCKER",
    "build_stale_lag_watchlist_report",
    "render_stale_lag_watchlist_markdown",
    "validate_stale_lag_watchlist_report",
    "write_stale_lag_watchlist_report",
]
