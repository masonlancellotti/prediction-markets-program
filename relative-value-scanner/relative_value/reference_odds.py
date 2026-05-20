from __future__ import annotations

from typing import Mapping


def american_to_implied_probability(odds: int | float) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (float(odds) + 100.0)
    return abs(float(odds)) / (abs(float(odds)) + 100.0)


def no_vig_probabilities(american_odds_by_outcome: Mapping[str, int | float]) -> dict[str, float]:
    if len(american_odds_by_outcome) < 2:
        raise ValueError("At least two outcomes are required for no-vig conversion")
    implied = {
        outcome: american_to_implied_probability(odds)
        for outcome, odds in american_odds_by_outcome.items()
    }
    total = sum(implied.values())
    if total <= 0:
        raise ValueError("Implied probability total must be positive")
    return {outcome: probability / total for outcome, probability in implied.items()}
