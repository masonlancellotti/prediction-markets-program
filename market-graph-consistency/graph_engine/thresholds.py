from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from graph_engine.formula import parse_fixture_market_formula
from graph_engine.models import MarketNode


THRESHOLD_LADDER_COMPARATORS = {">", ">=", "<", "<="}
UPWARD_COMPARATORS = {">", ">="}
DOWNWARD_COMPARATORS = {"<", "<="}


@dataclass(frozen=True)
class ThresholdCandidate:
    node: MarketNode
    family: str
    observable: str | None
    source: str | None
    window: str | None
    threshold: float | None
    comparator: str | None
    unit: str | None
    blockers: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return (
            self.threshold is not None
            and self.comparator in THRESHOLD_LADDER_COMPARATORS
            and bool(self.observable)
            and bool(self.source)
            and bool(self.window)
        )


def threshold_candidate_from_node(node: MarketNode) -> ThresholdCandidate:
    formula = parse_fixture_market_formula(node)
    metadata = _metadata(node)
    threshold = formula.threshold
    comparator = formula.comparator
    unit = formula.units
    family = formula.family if formula.family != "UNKNOWN" else _text(metadata.get("formula_family")) or "GENERIC_THRESHOLD"
    observable = formula.asset or formula.subject or _text(metadata.get("asset")) or _text(metadata.get("observable")) or node.observable
    source = formula.source or _text(metadata.get("source")) or node.settlement_source
    window = (
        formula.date
        or formula.meeting_date
        or formula.settlement_time
        or _text(metadata.get("date"))
        or _text(metadata.get("window"))
        or node.window
        or node.resolution_date
    )

    if threshold is None:
        threshold = _number(metadata.get("threshold"))
    if threshold is None:
        threshold = _number(metadata.get("payoff_state_threshold"))
    if threshold is None:
        threshold = _number(metadata.get("no_arb_threshold"))

    if comparator is None:
        comparator = _text(metadata.get("comparator"))
    if comparator is None:
        comparator = _text(metadata.get("payoff_state_comparator"))
    if comparator is None:
        comparator = _text(metadata.get("no_arb_comparator"))

    if unit is None:
        unit = _text(metadata.get("units")) or _text(metadata.get("unit"))
    if threshold is None or comparator is None or unit is None:
        parsed = _parse_threshold_text(f"{node.title} {node.canonical_text} {node.resolution_criteria}")
        if threshold is None:
            threshold = parsed.get("threshold")
        if comparator is None:
            comparator = parsed.get("comparator")
        if unit is None:
            unit = parsed.get("unit")

    if unit is None:
        unit = _unit_from_observable(observable)

    blockers: list[str] = []
    if threshold is None:
        blockers.append("missing_threshold")
    if comparator not in THRESHOLD_LADDER_COMPARATORS:
        blockers.append("missing_or_unsupported_threshold_comparator")
    if not observable:
        blockers.append("missing_threshold_observable")
    if not source:
        blockers.append("missing_threshold_source")
    if not window:
        blockers.append("missing_threshold_window")

    return ThresholdCandidate(
        node=node,
        family=str(family),
        observable=observable,
        source=source,
        window=window,
        threshold=float(threshold) if threshold is not None else None,
        comparator=comparator,
        unit=unit,
        blockers=tuple(sorted(set(blockers))),
    )


def threshold_group_blockers(candidates: list[ThresholdCandidate]) -> list[str]:
    blockers: list[str] = []
    comparators = {candidate.comparator for candidate in candidates}
    if None in comparators or len(comparators) > 1:
        blockers.append("mixed_threshold_comparators")
    units = {candidate.unit for candidate in candidates}
    if None in units or len(units) > 1:
        blockers.append("mixed_or_missing_threshold_units")
    blockers.extend(blocker for candidate in candidates for blocker in candidate.blockers)
    return sorted(set(blockers))


def ordered_ladder_candidates(candidates: list[ThresholdCandidate]) -> list[ThresholdCandidate]:
    comparator = candidates[0].comparator if candidates else None
    reverse = comparator in UPWARD_COMPARATORS
    return sorted(candidates, key=lambda candidate: candidate.threshold or 0.0, reverse=reverse)


def compile_market_formula_rows(nodes: Any) -> list[dict[str, Any]]:
    """Normalize market formula metadata into the shared report schema.

    ``nodes`` is intentionally permissive: callers may pass real MarketNode
    objects or saved report dictionaries that already carry formula metadata.
    """

    rows: dict[str, dict[str, Any]] = {}
    for item in nodes or []:
        row = _market_formula_row(item)
        if row is None:
            continue
        rows.setdefault(row["market_id"], row)
    return [rows[market_id] for market_id in sorted(rows)]


def _market_formula_row(item: Any) -> dict[str, Any] | None:
    if isinstance(item, MarketNode):
        return _market_formula_row_from_node(item)
    if isinstance(item, dict):
        return _market_formula_row_from_mapping(item)
    return None


def _market_formula_row_from_node(node: MarketNode) -> dict[str, Any]:
    candidate = threshold_candidate_from_node(node)
    asset = _canonical_asset(candidate.observable)
    return {
        "market_id": node.market_id,
        "family": candidate.family,
        "asset": asset or candidate.observable,
        "source": candidate.source,
        "date": candidate.window,
        "window": node.window,
        "comparator": candidate.comparator,
        "threshold": candidate.threshold,
        "unit": candidate.unit,
    }


def _market_formula_row_from_mapping(item: dict[str, Any]) -> dict[str, Any] | None:
    market_id = _first_text(item, ["market_id", "id"])
    if not market_id:
        return None
    text = _row_text(item)
    explicit_family = _first_text(item, ["family", "formula_family", "market_family", "event_family"])
    family = _normalise_family(explicit_family)
    asset = _canonical_asset(_first_text(item, ["asset", "subject", "observable"]))
    if not asset and (_word(text, "btc") or _word(text, "bitcoin")):
        asset = "BTC"
    if not family:
        if asset == "BTC" or _word(text, "btc") or _word(text, "bitcoin"):
            family = "BTC_THRESHOLD"
        elif _word(text, "fed") or _word(text, "fomc") or "fed funds" in text:
            family = "FED_MEETING_RANGE"
        else:
            family = "GENERIC_THRESHOLD"
    if family in {"BTC", "CRYPTO", "CRYPTO_THRESHOLD"}:
        family = "BTC_THRESHOLD"
    threshold = _optional_float_first(item, ["threshold", "strike", "target", "payoff_state_threshold", "no_arb_threshold"])
    comparator = _normalise_comparator(_first_text(item, ["comparator", "operator"]))
    parsed = _parse_threshold_text(text)
    if threshold is None:
        threshold = parsed.get("threshold")
    if comparator is None:
        comparator = parsed.get("comparator")
    source = _first_text(item, ["source", "settlement_source", "resolution_source", "index_source", "oracle", "data_source"])
    date = _first_text(item, ["date", "resolution_date", "event_date", "settlement_date"])
    window = _first_text(item, ["window", "settlement_window", "settlement_time", "close_resolution_time"])
    if not date:
        date = window
    unit = _first_text(item, ["unit", "units", "currency"])
    if not unit:
        unit = parsed.get("unit")
    if not unit and asset == "BTC":
        unit = "USD"
    return {
        "market_id": market_id,
        "family": family,
        "asset": asset or _first_text(item, ["asset", "subject", "observable"]) or None,
        "source": source or None,
        "date": date or None,
        "window": window or None,
        "comparator": comparator,
        "threshold": threshold,
        "unit": unit or None,
    }


def _metadata(node: MarketNode) -> dict[str, Any]:
    row = node.raw.get("normalized_row")
    if isinstance(row, dict):
        merged = dict(node.raw)
        merged.update(row)
        return merged
    return dict(node.raw)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_float_first(row: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value.replace(",", ""))
            except ValueError:
                continue
    return None


def _first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _row_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                parts.append(str(key))
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, (str, int, float)) and not isinstance(item, bool):
            parts.append(str(item))

    visit(value)
    return " ".join(parts).lower()


def _word(text: str, token: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text))


def _normalise_family(value: str) -> str:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "BTC": "BTC_THRESHOLD",
        "BITCOIN": "BTC_THRESHOLD",
        "CRYPTO": "BTC_THRESHOLD",
        "CRYPTO_THRESHOLD": "BTC_THRESHOLD",
    }
    return aliases.get(normalized, normalized)


def _canonical_asset(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    if normalized == "BITCOIN":
        return "BTC"
    return normalized


def _normalise_comparator(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in THRESHOLD_LADDER_COMPARATORS or normalized == "=":
        return normalized
    aliases = {
        "above": ">",
        "over": ">",
        "greater_than": ">",
        "greater than": ">",
        "at_least": ">=",
        "at least": ">=",
        "below": "<",
        "under": "<",
        "less_than": "<",
        "less than": "<",
        "at_most": "<=",
        "at most": "<=",
    }
    return aliases.get(normalized)


def _unit_from_observable(observable: str | None) -> str | None:
    if observable is None:
        return None
    normalized = observable.lower()
    if "btc" in normalized or "bitcoin" in normalized:
        return "USD"
    if "temperature" in normalized:
        return "F"
    if "metric" in normalized:
        return "metric"
    return None


def _parse_threshold_text(text: str) -> dict[str, Any]:
    normalized = text.lower().replace(",", "")
    patterns = [
        (">=", r"(?:at\s+least|at\s+or\s+above|not\s+below)\s+\$?([0-9]+(?:\.[0-9]+)?)(?:\s*(k|m|b|t|usd|f|%|percent))?"),
        ("<=", r"(?:at\s+most|at\s+or\s+below|not\s+above)\s+\$?([0-9]+(?:\.[0-9]+)?)(?:\s*(k|m|b|t|usd|f|%|percent))?"),
        (">", r"(?:above|over|greater\s+than|more\s+than|>)\s+\$?([0-9]+(?:\.[0-9]+)?)(?:\s*(k|m|b|t|usd|f|%|percent))?"),
        ("<", r"(?:below|under|less\s+than|<)\s+\$?([0-9]+(?:\.[0-9]+)?)(?:\s*(k|m|b|t|usd|f|%|percent))?"),
    ]
    for comparator, pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        value = float(match.group(1))
        suffix = match.group(2)
        unit = None
        if suffix == "k":
            value *= 1_000
            unit = "suffix:k"
        elif suffix == "m":
            value *= 1_000_000
            unit = "suffix:m"
        elif suffix == "b":
            value *= 1_000_000_000
            unit = "suffix:b"
        elif suffix == "t":
            value *= 1_000_000_000_000
            unit = "suffix:t"
        elif suffix in {"usd", "f", "%", "percent"}:
            unit = "percent" if suffix in {"%", "percent"} else suffix.upper()
        return {"threshold": value, "comparator": comparator, "unit": unit}
    return {"threshold": None, "comparator": None, "unit": None}


__all__ = [
    "compile_market_formula_rows",
    "DOWNWARD_COMPARATORS",
    "THRESHOLD_LADDER_COMPARATORS",
    "ThresholdCandidate",
    "ordered_ladder_candidates",
    "threshold_candidate_from_node",
    "threshold_group_blockers",
]
