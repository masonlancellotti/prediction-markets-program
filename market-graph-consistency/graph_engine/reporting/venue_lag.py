from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from graph_engine.formula import MarketFormula, parse_fixture_market_formula
from graph_engine.models import MarketNode, parse_datetime
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens
from graph_engine.snapshot_loader import SnapshotLoadError, load_schema_v1_snapshots


BANNER = "Diagnostic-only venue lag watchlist from saved files. Rows require manual review."
DEFAULT_STALE_SECONDS = 30 * 60
DEFAULT_PRICE_DELTA = 0.10
COMPARABLE_FAMILIES = {"BTC_THRESHOLD", "FED_MEETING_RANGE"}
FORMULA_RELATIONS = {
    "typed_formula_match_review_only",
    "threshold_ladder",
    "overlap_not_identical",
    "ambiguous_not_exact",
    "parse_blocked",
}


@dataclass(frozen=True)
class QuoteObservation:
    market_id: str
    venue: str
    formula: MarketFormula
    price: float | None
    quoted_at: datetime | None
    snapshot_as_of: datetime
    source_file: str


def build_venue_lag_watchlist_report(
    saved_paths: Iterable[Path | str],
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    price_delta_threshold: float = DEFAULT_PRICE_DELTA,
) -> dict[str, Any]:
    paths = [Path(path) for path in saved_paths]
    if len(paths) < 2:
        raise SchemaValidationError("venue lag watchlist requires two or more saved files")

    observations = _load_observations(paths)
    rows = _watchlist_rows(observations, stale_seconds=stale_seconds, price_delta_threshold=price_delta_threshold)
    rows = sorted(rows, key=lambda row: (-row["observed_price_delta"], row["market_ids"], row["formula_relation"]))
    for index, row in enumerate(rows, start=1):
        row["diagnostic_rank"] = index

    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "banner": BANNER,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "saved_file_count": len(paths),
        "stale_seconds": stale_seconds,
        "price_delta_threshold": round(price_delta_threshold, 6),
        "watchlist_count": len(rows),
        "venue_lag_watchlist": rows,
    }
    validate_venue_lag_watchlist_report(report)
    return report


def write_venue_lag_watchlist_report(
    saved_paths: Iterable[Path | str],
    json_output: Path | str,
    md_output: Path | str,
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    price_delta_threshold: float = DEFAULT_PRICE_DELTA,
) -> dict[str, Any]:
    report = build_venue_lag_watchlist_report(
        saved_paths,
        stale_seconds=stale_seconds,
        price_delta_threshold=price_delta_threshold,
    )
    validate_venue_lag_watchlist_report(report)

    json_path = Path(json_output)
    md_path = Path(md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_venue_lag_watchlist_markdown(report), encoding="utf-8")
    return report


def validate_venue_lag_watchlist_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("venue lag watchlist must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("venue lag watchlist must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("venue lag watchlist actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("venue_lag_watchlist")
    if not isinstance(rows, list):
        raise SchemaValidationError("venue_lag_watchlist must be a list")
    if report.get("watchlist_count") != len(rows):
        raise SchemaValidationError("watchlist_count must match venue_lag_watchlist")
    for index, row in enumerate(rows):
        _validate_row(row, f"venue_lag_watchlist[{index}]")


def render_venue_lag_watchlist_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Market Graph Venue Lag Watchlist",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Watchlist rows: {report['watchlist_count']}",
        "",
        "| Relation | Cap | Markets | Quote Age Seconds | Relative Age Seconds | Observed Price Delta | Blockers |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["venue_lag_watchlist"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["formula_relation"]),
                    _md(row["max_action_cap"]),
                    _md(", ".join(row["market_ids"])),
                    _md(row["quote_age_seconds"]),
                    _md(row["relative_age_seconds"]),
                    _md(row["observed_price_delta"]),
                    _md(", ".join(row["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _load_observations(paths: list[Path]) -> list[QuoteObservation]:
    observations: list[QuoteObservation] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") == 1:
            snapshot, _ = load_schema_v1_snapshots(snapshot_paths=[path])
            observations.extend(_observations_from_snapshot(path, snapshot.nodes.values()))
            continue
        if "formula_diagnostics" in payload:
            observations.extend(_observations_from_diagnostic(path, payload))
            continue
        raise SnapshotLoadError(f"{path.name}: unsupported saved file for venue lag watchlist")
    return observations


def _observations_from_snapshot(path: Path, nodes: Iterable[MarketNode]) -> list[QuoteObservation]:
    observations: list[QuoteObservation] = []
    for node in nodes:
        formula = parse_fixture_market_formula(node)
        observations.append(
            QuoteObservation(
                market_id=node.market_id,
                venue=node.venue,
                formula=formula,
                price=_node_price(node),
                quoted_at=node.as_of,
                snapshot_as_of=node.as_of,
                source_file=path.name,
            )
        )
    return observations


def _observations_from_diagnostic(path: Path, payload: dict[str, Any]) -> list[QuoteObservation]:
    formula_report = payload.get("formula_diagnostics", {})
    rows = formula_report.get("formulas", []) if isinstance(formula_report, dict) else []
    observations: list[QuoteObservation] = []
    snapshot_as_of = _optional_datetime(payload.get("generated_at")) or _optional_datetime(payload.get("as_of")) or datetime.min
    for row in rows:
        if not isinstance(row, dict):
            continue
        market_id = str(row.get("market_id") or f"unknown:{len(observations)}")
        observations.append(
            QuoteObservation(
                market_id=market_id,
                venue=market_id.split(":", 1)[0] if ":" in market_id else "unknown",
                formula=_formula_from_row(row),
                price=None,
                quoted_at=None,
                snapshot_as_of=snapshot_as_of,
                source_file=path.name,
            )
        )
    return observations


def _watchlist_rows(
    observations: list[QuoteObservation],
    *,
    stale_seconds: int,
    price_delta_threshold: float,
) -> list[dict[str, Any]]:
    histories: dict[str, list[QuoteObservation]] = defaultdict(list)
    for observation in observations:
        histories[observation.market_id].append(observation)
    for history in histories.values():
        history.sort(key=lambda item: (item.snapshot_as_of, item.source_file))

    latest = [history[-1] for history in histories.values()]
    newest_snapshot_time = max((item.snapshot_as_of for item in latest), default=None)
    if newest_snapshot_time is None:
        return []

    deltas = {
        market_id: _observed_delta(history)
        for market_id, history in histories.items()
    }

    rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(latest):
        for right in latest[left_index + 1 :]:
            if left.market_id == right.market_id:
                continue
            relation, relation_blockers = _formula_relation(left.formula, right.formula)
            if relation is None:
                continue
            quote_ages = [_quote_age_seconds(left, newest_snapshot_time), _quote_age_seconds(right, newest_snapshot_time)]
            if None in quote_ages:
                continue
            quote_age_seconds = max(quote_ages)  # type: ignore[arg-type]
            relative_age_seconds = abs(quote_ages[0] - quote_ages[1])  # type: ignore[operator]
            observed_price_delta = max(deltas.get(left.market_id, 0.0), deltas.get(right.market_id, 0.0))
            if quote_age_seconds <= stale_seconds or relative_age_seconds <= 0 or observed_price_delta < price_delta_threshold:
                continue
            rows.append(
                _row(
                    left,
                    right,
                    relation,
                    int(quote_age_seconds),
                    int(relative_age_seconds),
                    observed_price_delta,
                    relation_blockers,
                    stale_seconds,
                    price_delta_threshold,
                )
            )
    return rows


def _row(
    left: QuoteObservation,
    right: QuoteObservation,
    relation: str,
    quote_age_seconds: int,
    relative_age_seconds: int,
    observed_price_delta: float,
    blockers: list[str],
    stale_seconds: int,
    price_delta_threshold: float,
) -> dict[str, Any]:
    market_ids = sorted([left.market_id, right.market_id])
    row = {
        "watchlist_id": f"venue_lag:{market_ids[0]}:{market_ids[1]}",
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": "WATCH",
        "diagnostic_priority": "WATCH",
        "formula_relation": relation,
        "market_ids": market_ids,
        "source_files": sorted([left.source_file, right.source_file]),
        "quote_age_seconds": quote_age_seconds,
        "relative_age_seconds": relative_age_seconds,
        "observed_price_delta": round(observed_price_delta, 6),
        "stale_seconds": stale_seconds,
        "price_delta_threshold": round(price_delta_threshold, 6),
        "blockers": list(blockers),
        "required_review_questions": [
            "Do both markets share the same observable and settlement source?",
            "Is the stale quote timestamp from a saved file rather than a current venue state?",
            "Did the related market movement occur within the same saved-file window?",
            "Do formula blockers prevent interpreting the pair as comparable?",
        ],
        "reason_for_review": "Saved files show stale quote timing alongside related market movement.",
    }
    _validate_row(row, "venue_lag_watchlist[]")
    return row


def _formula_relation(left: MarketFormula, right: MarketFormula) -> tuple[str | None, list[str]]:
    if left.family not in COMPARABLE_FAMILIES or right.family not in COMPARABLE_FAMILIES:
        return None, []
    blockers = sorted(set(left.blockers + right.blockers))
    if blockers:
        return "parse_blocked", blockers
    if left.family != right.family:
        return None, []
    if left.family == "BTC_THRESHOLD":
        if left.asset != right.asset:
            return None, []
        if left.source != right.source or left.date != right.date:
            return "ambiguous_not_exact", ["source_or_date_mismatch"]
        if left.threshold == right.threshold and left.comparator == right.comparator:
            return "typed_formula_match_review_only", []
        return "threshold_ladder", []
    if left.family == "FED_MEETING_RANGE":
        if left.subject != right.subject:
            return None, []
        if left.source != right.source or left.meeting_date != right.meeting_date:
            return "ambiguous_not_exact", ["source_or_meeting_mismatch"]
        if left.lower_bound == right.lower_bound and left.upper_bound == right.upper_bound:
            return "typed_formula_match_review_only", []
        if _ranges_overlap(left, right):
            return "overlap_not_identical", ["range_overlap_not_identical"]
        return "ambiguous_not_exact", ["range_mismatch"]
    return None, []


def _validate_row(row: dict[str, Any], path: str) -> None:
    required = [
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "formula_relation",
        "quote_age_seconds",
        "relative_age_seconds",
        "observed_price_delta",
        "blockers",
        "required_review_questions",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if row.get("max_action_cap") not in {None, *DIAGNOSTIC_HINT_ACTIONS}:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
    if row["formula_relation"] not in FORMULA_RELATIONS:
        raise SchemaValidationError(f"{path}.formula_relation is not allowed")
    for key in ["quote_age_seconds", "relative_age_seconds"]:
        if not isinstance(row[key], int) or isinstance(row[key], bool) or row[key] < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative integer")
    if not isinstance(row["observed_price_delta"], (int, float)) or isinstance(row["observed_price_delta"], bool) or row["observed_price_delta"] < 0:
        raise SchemaValidationError(f"{path}.observed_price_delta must be a non-negative number")
    if not isinstance(row["blockers"], list) or not all(isinstance(item, str) for item in row["blockers"]):
        raise SchemaValidationError(f"{path}.blockers must be a list of strings")
    questions = row["required_review_questions"]
    if not isinstance(questions, list) or not questions or not all(isinstance(item, str) and item for item in questions):
        raise SchemaValidationError(f"{path}.required_review_questions must contain strings")
    _reject_prohibited_tokens(row)


def _formula_from_row(row: dict[str, Any]) -> MarketFormula:
    return MarketFormula(
        market_id=str(row.get("market_id")),
        family=str(row.get("family")),
        subject=row.get("subject"),
        asset=row.get("asset"),
        source=row.get("source"),
        date=row.get("date"),
        meeting_date=row.get("meeting_date"),
        settlement_time=row.get("settlement_time"),
        comparator=row.get("comparator"),
        threshold=row.get("threshold"),
        lower_bound=row.get("lower_bound"),
        upper_bound=row.get("upper_bound"),
        units=row.get("units"),
        side=row.get("side", "YES"),
        parse_quality=float(row.get("parse_quality", 0.0)),
        blockers=list(row.get("blockers", [])),
        provenance=row.get("provenance") if isinstance(row.get("provenance"), dict) else None,
    )


def _node_price(node: MarketNode) -> float | None:
    try:
        return node.probability
    except ValueError:
        return None


def _quote_age_seconds(observation: QuoteObservation, newest_snapshot_time: datetime) -> int | None:
    if observation.quoted_at is None:
        return None
    return max(0, int((newest_snapshot_time - observation.quoted_at).total_seconds()))


def _observed_delta(history: list[QuoteObservation]) -> float:
    priced = [item for item in history if item.price is not None]
    if len(priced) < 2:
        return 0.0
    previous, latest = priced[-2], priced[-1]
    return round(abs(float(latest.price) - float(previous.price)), 6)


def _ranges_overlap(left: MarketFormula, right: MarketFormula) -> bool:
    if None in {left.lower_bound, left.upper_bound, right.lower_bound, right.upper_bound}:
        return False
    return max(float(left.lower_bound), float(right.lower_bound)) < min(float(left.upper_bound), float(right.upper_bound))


def _optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_datetime(str(value), "saved_file_time")
    except (TypeError, ValueError):
        return None


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
