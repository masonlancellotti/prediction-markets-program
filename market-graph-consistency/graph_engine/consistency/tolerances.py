from __future__ import annotations

from graph_engine.models import Action, ViolationKind


DEFAULT_EDGE_TOLERANCE = 0.03
DEFAULT_REWORD_TOLERANCE = 0.04
DEFAULT_EXCLUSION_TOLERANCE = 0.03
DEFAULT_MARKET_SIGNAL_CONFIDENCE = 0.95
SPREAD_BUFFER_MULTIPLIER = 0.5


def action_for_violation(kind: ViolationKind, confidence: float, magnitude: float) -> Action:
    if kind == ViolationKind.AMBIGUOUS_WORDING:
        if confidence >= 0.30:
            return Action.WATCH
        return Action.IGNORE

    if confidence < 0.25 or magnitude < 0.005:
        return Action.IGNORE
    if confidence >= 0.60 and magnitude >= 0.025:
        return Action.MANUAL_REVIEW
    if confidence >= 0.30 and magnitude >= 0.01:
        return Action.WATCH
    return Action.IGNORE


def spread_buffer(*spreads: float) -> float:
    if not spreads:
        return 0.0
    return SPREAD_BUFFER_MULTIPLIER * sum(max(0.0, spread) for spread in spreads)
