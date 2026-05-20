from __future__ import annotations


def clamp_confidence(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def combine_confidences(*values: float) -> float:
    combined = 1.0
    for value in values:
        combined *= clamp_confidence(value)
    return clamp_confidence(combined)


def confidence_band(value: float) -> str:
    value = clamp_confidence(value)
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"

