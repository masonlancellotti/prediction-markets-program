"""Normalized contract-grammar layer for crypto markets.

Classifies a market by *grammar first* — what state variable resolves the
payoff — not by coin or platform. Titles are weak evidence; rules / settlement
source / timing define the payoff, so classification prefers explicit
observation/window fields and only falls back to text hints.

Primitive families:
  - terminal_threshold   : "above/below K at time T"     (state: final price P_T)
  - terminal_range       : "between L and U at time T"    (state: final price P_T)
  - directional_return   : "up/down vs reference/open"    (state: return P_ref->P_T)
  - barrier_touch        : "hit/touch/reach K within T"    (state: path max/min)
  - unknown

Terminal threshold and terminal range share the terminal-price state variable
and may be combined into one payoff-vector space. Directional return may only
be compared to another directional return when reference_start_utc,
target_instant_utc, interval length, tie rule and reference-price semantics
align. Barrier/touch is path-dependent and must never be mixed with the others.
"""
from __future__ import annotations

from typing import Any


CONTRACT_FAMILY_TERMINAL_THRESHOLD = "terminal_threshold"
CONTRACT_FAMILY_TERMINAL_RANGE = "terminal_range"
CONTRACT_FAMILY_DIRECTIONAL_RETURN = "directional_return"
CONTRACT_FAMILY_BARRIER_TOUCH = "barrier_touch"
CONTRACT_FAMILY_UNKNOWN = "unknown"

# Terminal-price families share one payoff-vector state space.
TERMINAL_FAMILIES = (CONTRACT_FAMILY_TERMINAL_THRESHOLD, CONTRACT_FAMILY_TERMINAL_RANGE)

# Strong text hints. Barrier wins over everything because a path-dependent
# "hit/touch/reach" payoff is fundamentally different even if the title also
# names a strike.
_BARRIER_HINTS = ("hit ", "hits ", "touch", "reach", "barrier", "at any point", "anytime", "ever reach", "ever hit", "high of", "low of", "all-time high", "all time high")
_UPDOWN_HINTS = ("up or down", "up/down", "above its open", "below its open", "higher than its open", "lower than its open", "vs open", "from the open", "from its open", "open price", "reference price")
_RANGE_HINTS = ("between ", " to $", " and $", "range", "bucket")


def classify_contract_family(
    *,
    payoff_observation_type: str | None = None,
    market_shape: str | None = None,
    comparator: str | None = None,
    threshold_value: Any = None,
    lower_bound: Any = None,
    upper_bound: Any = None,
    rules_text: str | None = None,
    title: str | None = None,
) -> str:
    text = f"{title or ''} {rules_text or ''}".lower()
    obs = str(payoff_observation_type or "").lower()
    shape = str(market_shape or "").lower()
    comp = str(comparator or "").lower()

    # 1. Barrier / touch is path-dependent — strongest signal, classify first.
    if obs == "touch_before_deadline" or "touch" in shape or "deadline" in shape or any(h in text for h in _BARRIER_HINTS):
        return CONTRACT_FAMILY_BARRIER_TOUCH

    # 2. Directional return (up/down vs a reference/open price).
    if obs == "interval_start_to_end_change" or comp in ("up", "down") or "up_down" in shape or any(h in text for h in _UPDOWN_HINTS):
        return CONTRACT_FAMILY_DIRECTIONAL_RETURN

    # 3. Terminal range (final price inside [L, U]).
    if (
        obs == "range_at_target"
        or "range" in shape
        or comp == "range"
        or (lower_bound is not None and upper_bound is not None)
        or (any(h in text for h in _RANGE_HINTS) and "above" not in text and "below" not in text)
    ):
        return CONTRACT_FAMILY_TERMINAL_RANGE

    # 4. Terminal threshold (final price above/below K).
    if obs == "point_in_time_at_target" or comp in ("above", "below") or threshold_value is not None:
        return CONTRACT_FAMILY_TERMINAL_THRESHOLD

    return CONTRACT_FAMILY_UNKNOWN


def normalize_contract_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project an interval typed-key row into the normalized grammar schema."""
    q = row.get("quote") or {}
    comparator = str(row.get("comparator") or "unknown").lower()
    threshold = _to_float(row.get("threshold_or_strike"))
    floor = _to_float(row.get("bucket_floor"))
    cap = _to_float(row.get("bucket_cap"))
    obs = row.get("payoff_observation_type")
    family = classify_contract_family(
        payoff_observation_type=obs,
        market_shape=row.get("market_shape"),
        comparator=comparator,
        threshold_value=threshold if obs == "point_in_time_at_target" else None,
        lower_bound=floor,
        upper_bound=cap,
        rules_text=row.get("rules_text"),
        title=row.get("market_id_or_ticker"),
    )
    direction = comparator if comparator in ("above", "below", "up", "down", "range", "touch") else "unknown"
    inclusivity = _inclusivity(family, comparator)
    return {
        "platform": row.get("platform"),
        "asset": row.get("asset"),
        "contract_family": family,
        "market_shape": row.get("market_shape"),
        "payoff_observation_type": obs,
        "observation_start_utc": row.get("reference_start_utc"),
        "reference_start_utc": row.get("reference_start_utc"),
        "target_instant_utc": row.get("target_instant_utc"),
        "settlement_time_utc": row.get("settlement_time_utc") or row.get("target_instant_utc"),
        "timezone": row.get("timezone") or "America/New_York",
        "settlement_source": row.get("settlement_source") or row.get("price_source"),
        "price_source": row.get("price_source"),
        "threshold_value": threshold if family == CONTRACT_FAMILY_TERMINAL_THRESHOLD else None,
        "lower_bound": floor if family == CONTRACT_FAMILY_TERMINAL_RANGE else None,
        "upper_bound": cap if family == CONTRACT_FAMILY_TERMINAL_RANGE else None,
        "reference_price": _to_float(row.get("reference_price")),
        "reference_lock_time": row.get("reference_start_utc"),
        "direction": direction,
        "inclusivity": inclusivity,
        "tie_rule": row.get("tie_rule") or "unknown",
        "yes_bid": _to_float(q.get("yes_bid")),
        "yes_ask": _to_float(q.get("yes_ask")),
        "no_bid": _to_float(q.get("no_bid")),
        "no_ask": _to_float(q.get("no_ask")),
        "bid_size": _to_float(q.get("yes_bid_size")),
        "ask_size": _to_float(q.get("yes_ask_size")),
        "quote_timestamp": q.get("quote_timestamp"),
        "depth_status": q.get("depth_status"),
        "blockers": list(q.get("blockers_remaining") or []),
    }


def families_compatible(family_a: str, family_b: str) -> bool:
    """Two families may be *directly* compared only if identical, or both are
    terminal-price families (threshold/range share the same state variable)."""
    if family_a == family_b:
        return family_a not in (CONTRACT_FAMILY_BARRIER_TOUCH, CONTRACT_FAMILY_UNKNOWN)
    return family_a in TERMINAL_FAMILIES and family_b in TERMINAL_FAMILIES


def _inclusivity(family: str, comparator: str) -> str:
    if family == CONTRACT_FAMILY_TERMINAL_RANGE:
        return "(lower, upper]"
    if comparator == "above":
        return ">"
    if comparator == "below":
        return "<="
    if comparator in ("up", "down"):
        return comparator
    if comparator == "touch":
        return "touch"
    return "unknown"


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
