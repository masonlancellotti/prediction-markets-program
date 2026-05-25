from __future__ import annotations

import re
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode
from graph_engine.reporting.schema_validation import (
    SchemaValidationError,
    _reject_prohibited_tokens,
    validate_formula_diagnostics_contract,
)


ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
COMPARABLE_FAMILIES = {"BTC_THRESHOLD", "FED_MEETING_RANGE"}
SUPPORTED_FAMILIES = {"BTC_THRESHOLD", "FED_MEETING_RANGE", "SPORTS_CHAMPION", "WEATHER_RANGE", "UNKNOWN"}
SUPPORTED_COMPARATORS = {">", ">=", "<", "<=", "=", "in_range", None}
MIN_PROPOSAL_PARSE_QUALITY = 0.7


@dataclass
class MarketFormula:
    market_id: str
    family: str
    subject: str | None = None
    asset: str | None = None
    team: str | None = None
    location: str | None = None
    source: str | None = None
    date: str | None = None
    meeting_date: str | None = None
    settlement_time: str | None = None
    comparator: str | None = None
    threshold: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    units: str | None = None
    side: str = "YES"
    parse_quality: float = 0.0
    blockers: list[str] = field(default_factory=list)
    provenance: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "family": self.family,
            "subject": self.subject,
            "asset": self.asset,
            "team": self.team,
            "location": self.location,
            "source": self.source,
            "date": self.date,
            "meeting_date": self.meeting_date,
            "settlement_time": self.settlement_time,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "units": self.units,
            "side": self.side,
            "parse_quality": round(self.parse_quality, 3),
            "blockers": list(self.blockers),
            "provenance": self.provenance,
        }


def parse_fixture_market_formula(node: MarketNode) -> MarketFormula:
    text = f"{node.title} {node.canonical_text}".lower()
    if "btc" in text or "bitcoin" in text:
        return _parse_btc_threshold(node, text)
    if "fed" in text or "federal reserve" in text or "fed funds" in text:
        return _parse_fed_meeting_range(node, text)
    if "wins" in text or "champion" in text:
        return MarketFormula(
            market_id=node.market_id,
            family="SPORTS_CHAMPION",
            subject=node.entities[0] if node.entities else None,
            team=node.entities[0] if node.entities else None,
            date=node.resolution_date,
            source=node.settlement_source,
            parse_quality=0.5,
            blockers=["fixture_parser_not_specific_enough"],
        )
    if "turnout" in text or "weather" in text:
        return MarketFormula(
            market_id=node.market_id,
            family="WEATHER_RANGE" if "weather" in text else "UNKNOWN",
            subject=node.observable,
            date=node.resolution_date,
            source=node.settlement_source,
            parse_quality=0.4,
            blockers=["fixture_parser_not_specific_enough"],
        )
    return MarketFormula(
        market_id=node.market_id,
        family="UNKNOWN",
        source=node.settlement_source,
        date=node.resolution_date,
        parse_quality=0.0,
        blockers=["unsupported_fixture_title"],
    )


def build_formula_diagnostics_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    formulas = [parse_fixture_market_formula(node) for node in snapshot.nodes.values()]
    return build_formula_diagnostics_report_from_formulas(formulas)


def build_formula_diagnostics_report_from_formulas(formulas: list[MarketFormula]) -> dict[str, Any]:
    from graph_engine.formula_clusters import build_formula_cluster_constraints_report

    formula_rows = [formula.to_dict() for formula in sorted(formulas, key=lambda item: item.market_id)]
    diagnostics = _compare_formulas(formulas)
    cluster_report = build_formula_cluster_constraints_report(formulas)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ALLOWED_ACTIONS,
        "formula_count": len(formula_rows),
        "comparison_count": len(diagnostics),
        "formula_cluster_constraint_count": cluster_report["cluster_constraint_count"],
        "formulas": formula_rows,
        "formula_diagnostics": diagnostics,
        "formula_cluster_constraints": cluster_report["formula_cluster_constraints"],
    }
    validate_formula_diagnostics_contract(report)
    return report


def load_proposed_market_formulas(path: Path | str) -> list[MarketFormula]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return import_proposed_market_formulas(payload)


def import_proposed_market_formulas(payload: Any) -> list[MarketFormula]:
    _reject_prohibited_tokens(payload)
    if not isinstance(payload, dict):
        raise SchemaValidationError("proposed formula payload must be an object")
    if payload.get("diagnostic_only") is not True:
        raise SchemaValidationError("proposed formula payload must be diagnostic_only")
    if payload.get("allowed_actions") != ALLOWED_ACTIONS:
        raise SchemaValidationError("proposed formula actions must be WATCH and MANUAL_REVIEW only")
    if payload.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("proposed formulas must not affect evaluator gates")
    proposals = payload.get("proposed_formulas")
    if not isinstance(proposals, list):
        raise SchemaValidationError("proposed_formulas must be a list")
    formulas = [_validate_proposed_formula(item, f"proposed_formulas[{index}]") for index, item in enumerate(proposals)]
    if payload.get("formula_count") not in {None, len(formulas)}:
        raise SchemaValidationError("formula_count must match proposed_formulas length")
    return formulas


def _validate_proposed_formula(item: Any, path: str) -> MarketFormula:
    _reject_prohibited_tokens(item)
    if not isinstance(item, dict):
        raise SchemaValidationError(f"{path} must be an object")
    _reject_exact_payoff_claims(item, path)

    allowed_keys = {
        "market_id",
        "family",
        "subject",
        "asset",
        "team",
        "location",
        "source",
        "date",
        "meeting_date",
        "settlement_time",
        "comparator",
        "threshold",
        "lower_bound",
        "upper_bound",
        "units",
        "side",
        "parse_quality",
        "confidence",
        "blockers",
        "provenance",
    }
    extra = sorted(set(item) - allowed_keys)
    if extra:
        raise SchemaValidationError(f"{path} has unsupported fields {extra!r}")

    family = item.get("family")
    if family not in SUPPORTED_FAMILIES:
        raise SchemaValidationError(f"{path}.family is not supported")
    comparator = item.get("comparator")
    if comparator not in SUPPORTED_COMPARATORS:
        raise SchemaValidationError(f"{path}.comparator is not supported")
    side = item.get("side")
    if side not in {"YES", "NO"}:
        raise SchemaValidationError(f"{path}.side must be YES or NO")

    parse_quality = item.get("parse_quality", item.get("confidence"))
    if not isinstance(parse_quality, (int, float)) or isinstance(parse_quality, bool):
        raise SchemaValidationError(f"{path}.parse_quality must be numeric")
    if parse_quality < MIN_PROPOSAL_PARSE_QUALITY:
        raise SchemaValidationError(f"{path}.parse_quality is below fixture import threshold")

    blockers = item.get("blockers", [])
    if not isinstance(blockers, list) or not all(isinstance(blocker, str) for blocker in blockers):
        raise SchemaValidationError(f"{path}.blockers must be a list of strings")
    provenance = item.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise SchemaValidationError(f"{path}.provenance must be a non-empty object")
    _reject_exact_payoff_claims(provenance, f"{path}.provenance")

    threshold = _optional_number(item.get("threshold"), f"{path}.threshold")
    lower_bound = _optional_number(item.get("lower_bound"), f"{path}.lower_bound")
    upper_bound = _optional_number(item.get("upper_bound"), f"{path}.upper_bound")

    if threshold is not None and not item.get("units"):
        raise SchemaValidationError(f"{path}.units is required with threshold")
    if family == "BTC_THRESHOLD":
        for key in ["asset", "source", "date", "comparator", "threshold", "units"]:
            if item.get(key) in {None, ""}:
                raise SchemaValidationError(f"{path}.{key} is required for BTC_THRESHOLD")
        if item.get("asset") != "BTC":
            raise SchemaValidationError(f"{path}.asset must be BTC for BTC_THRESHOLD")
        if comparator not in {">", ">=", "<", "<=", "="}:
            raise SchemaValidationError(f"{path}.comparator is not valid for BTC_THRESHOLD")
    if family == "FED_MEETING_RANGE":
        for key in ["subject", "source", "meeting_date", "comparator", "lower_bound", "upper_bound", "units"]:
            if item.get(key) in {None, ""}:
                raise SchemaValidationError(f"{path}.{key} is required for FED_MEETING_RANGE")
        if comparator != "in_range":
            raise SchemaValidationError(f"{path}.comparator must be in_range for FED_MEETING_RANGE")
        if lower_bound is None or upper_bound is None or lower_bound >= upper_bound:
            raise SchemaValidationError(f"{path}.range bounds are invalid")

    return MarketFormula(
        market_id=_required_string(item, "market_id", path),
        family=family,
        subject=item.get("subject"),
        asset=item.get("asset"),
        team=item.get("team"),
        location=item.get("location"),
        source=item.get("source"),
        date=item.get("date"),
        meeting_date=item.get("meeting_date"),
        settlement_time=item.get("settlement_time"),
        comparator=comparator,
        threshold=threshold,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        units=item.get("units"),
        side=side,
        parse_quality=float(parse_quality),
        blockers=blockers,
        provenance=provenance,
    )


def _reject_exact_payoff_claims(payload: Any, path: str) -> None:
    forbidden = {"exact_same_payoff", "exact_same_payoff_evidence", "same_payoff_evidence", "trusted_equality"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in forbidden:
                raise SchemaValidationError(f"{path}.{key} cannot claim exact same-payoff evidence")
            if isinstance(value, str) and any(token in value.lower().replace("-", "_") for token in forbidden):
                raise SchemaValidationError(f"{path}.{key} cannot claim exact same-payoff evidence")


def _optional_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SchemaValidationError(f"{path} must be numeric")
    return float(value)


def _required_string(item: dict[str, Any], key: str, path: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise SchemaValidationError(f"{path}.{key} must be a non-empty string")
    return value


def _parse_btc_threshold(node: MarketNode, text: str) -> MarketFormula:
    blockers: list[str] = []
    threshold = _first_number_after(text, ["above", "over", "greater than", "at least"])
    comparator = ">=" if "at least" in text else ">"
    if threshold is None:
        blockers.append("missing_threshold")
    if not node.settlement_source:
        blockers.append("missing_source")
    if not node.window and not node.resolution_date:
        blockers.append("missing_date")
    return MarketFormula(
        market_id=node.market_id,
        family="BTC_THRESHOLD",
        subject="BTC",
        asset="BTC",
        source=node.settlement_source,
        date=node.window or node.resolution_date,
        settlement_time=node.window,
        comparator=comparator if threshold is not None else None,
        threshold=threshold,
        units="USD",
        side="YES",
        parse_quality=0.95 if not blockers else 0.55,
        blockers=blockers,
    )


def _parse_fed_meeting_range(node: MarketNode, text: str) -> MarketFormula:
    blockers: list[str] = []
    meeting_date = node.raw.get("meeting_date") or node.window or node.resolution_date
    lower_bound = node.raw.get("lower_bound")
    upper_bound = node.raw.get("upper_bound")
    if lower_bound is None or upper_bound is None:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:-|to)\s*([0-9]+(?:\.[0-9]+)?)", text)
        if match:
            lower_bound = float(match.group(1))
            upper_bound = float(match.group(2))
    if lower_bound is None or upper_bound is None:
        blockers.append("missing_range")
    if not meeting_date:
        blockers.append("missing_meeting_date")
    if not node.settlement_source:
        blockers.append("missing_source")
    return MarketFormula(
        market_id=node.market_id,
        family="FED_MEETING_RANGE",
        subject="FED_FUNDS",
        source=node.settlement_source,
        meeting_date=str(meeting_date) if meeting_date else None,
        settlement_time=node.window,
        comparator="in_range" if lower_bound is not None and upper_bound is not None else None,
        lower_bound=float(lower_bound) if lower_bound is not None else None,
        upper_bound=float(upper_bound) if upper_bound is not None else None,
        units="percent",
        side="YES",
        parse_quality=0.95 if not blockers else 0.5,
        blockers=blockers,
    )


def _compare_formulas(formulas: list[MarketFormula]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for left_index, left in enumerate(formulas):
        for right in formulas[left_index + 1 :]:
            if left.family not in COMPARABLE_FAMILIES or right.family != left.family:
                continue
            diagnostic = _compare_pair(left, right)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
    return sorted(diagnostics, key=lambda item: item["comparison_id"])


def _compare_pair(left: MarketFormula, right: MarketFormula) -> dict[str, Any] | None:
    blockers = sorted(set(left.blockers + right.blockers))
    if blockers:
        return _diagnostic(left, right, "parse_blocked", "WATCH", blockers, "Required formula fields are missing.")
    if left.family == "BTC_THRESHOLD":
        return _compare_btc(left, right)
    if left.family == "FED_MEETING_RANGE":
        return _compare_fed(left, right)
    return None


def _compare_btc(left: MarketFormula, right: MarketFormula) -> dict[str, Any] | None:
    if left.source != right.source or left.date != right.date:
        return _diagnostic(left, right, "ambiguous_not_exact", "WATCH", ["source_or_date_mismatch"], "BTC formulas differ by source or date.")
    if left.threshold == right.threshold and left.comparator == right.comparator:
        return _diagnostic(left, right, "typed_formula_match_review_only", "MANUAL_REVIEW", [], "Typed BTC formulas match, but graph output remains diagnostic only.")
    return _diagnostic(left, right, "threshold_ladder", "MANUAL_REVIEW", [], "BTC thresholds share source and date but use different thresholds.")


def _compare_fed(left: MarketFormula, right: MarketFormula) -> dict[str, Any] | None:
    if left.meeting_date != right.meeting_date or left.source != right.source:
        return _diagnostic(left, right, "ambiguous_not_exact", "WATCH", ["source_or_meeting_mismatch"], "Fed formulas differ by meeting or source.")
    if left.lower_bound == right.lower_bound and left.upper_bound == right.upper_bound:
        return _diagnostic(left, right, "typed_formula_match_review_only", "MANUAL_REVIEW", [], "Typed Fed formulas match, but graph output remains diagnostic only.")
    if _ranges_overlap(left, right):
        return _diagnostic(left, right, "overlap_not_identical", "WATCH", ["range_overlap_not_identical"], "Fed ranges overlap but are not identical.")
    return _diagnostic(left, right, "disjoint_ranges", "WATCH", ["range_mismatch"], "Fed ranges are not identical.")


def _diagnostic(
    left: MarketFormula,
    right: MarketFormula,
    relation: str,
    priority: str,
    blockers: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "comparison_id": f"formula:{left.market_id}->{right.market_id}",
        "market_ids": [left.market_id, right.market_id],
        "family": left.family,
        "formula_relation": relation,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ALLOWED_ACTIONS,
        "max_action_cap": priority,
        "diagnostic_priority": priority,
        "blockers": list(blockers),
        "review_reason": reason,
    }


def _ranges_overlap(left: MarketFormula, right: MarketFormula) -> bool:
    if None in {left.lower_bound, left.upper_bound, right.lower_bound, right.upper_bound}:
        return False
    return max(left.lower_bound, right.lower_bound) < min(left.upper_bound, right.upper_bound)  # type: ignore[arg-type]


def _first_number_after(text: str, prefixes: list[str]) -> float | None:
    for prefix in prefixes:
        match = re.search(rf"{re.escape(prefix)}\s+\$?([0-9]+(?:\.[0-9]+)?)\s*(k|m|b|t)?", text)
        if not match:
            continue
        value = float(match.group(1))
        suffix = match.group(2)
        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000
        elif suffix == "b":
            value *= 1_000_000_000
        elif suffix == "t":
            value *= 1_000_000_000_000
        return value
    return None
