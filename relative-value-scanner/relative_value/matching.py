from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from relative_value.config import ScannerConfig
from relative_value.models import MatchAssessment, NormalizedMarket
from relative_value.normalize import normalize_text, token_set


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "vs",
    "will",
}
_POSITIVE_POLARITY = {
    "over",
    "above",
    "at_least",
    "yes",
    "will",
    "goes",
    "reaches",
    "wins",
    "win",
    "beats",
    "beat",
    "defeats",
    "advances",
    "holds",
    "passes",
    "exceeds",
    "hits",
}
_NEGATIVE_POLARITY = {
    "under",
    "below",
    "at_most",
    "no",
    "not",
    "do_not",
    "does_not",
    "is_not",
    "are_not",
    "was_not",
    "were_not",
    "has_not",
    "had_not",
    "loses",
    "lose",
    "defeated",
    "eliminated",
    "drops",
    "fails",
    "misses",
    "falls",
}
_COMPARATOR_TOKENS = _POSITIVE_POLARITY | _NEGATIVE_POLARITY | {"at", "least", "most", "does"}


@dataclass(frozen=True)
class TextComparison:
    score: float
    reasons: tuple[str, ...] = ()
    same_side: bool = False
    opposite_side: bool = False


def _canonical_text(value: str) -> str:
    value = value.lower()
    contractions = {
        "doesn't": "does not",
        "don't": "do not",
        "won't": "will not",
        "isn't": "is not",
        "aren't": "are not",
        "wasn't": "was not",
        "weren't": "were not",
        "hasn't": "has not",
        "hadn't": "had not",
    }
    for contraction, expanded in contractions.items():
        value = value.replace(contraction, expanded)
    normalized = normalize_text(value)
    normalized = normalized.replace("at least", "at_least")
    normalized = normalized.replace("at most", "at_most")
    normalized = normalized.replace("do not", "do_not")
    normalized = normalized.replace("does not", "does_not")
    normalized = normalized.replace("is not", "is_not")
    normalized = normalized.replace("are not", "are_not")
    normalized = normalized.replace("was not", "was_not")
    normalized = normalized.replace("were not", "were_not")
    normalized = normalized.replace("has not", "has_not")
    normalized = normalized.replace("had not", "had_not")
    return normalized


def _numbers(value: str) -> set[str]:
    return set(_NUMBER_RE.findall(value))


def _polarity(tokens: set[str]) -> str | None:
    has_positive = bool(tokens & _POSITIVE_POLARITY)
    has_negative = bool(tokens & _NEGATIVE_POLARITY)
    if has_negative and not has_positive:
        return "negative"
    if has_positive and not has_negative:
        return "positive"
    if has_negative:
        return "negative"
    return None


def _content_tokens(value: str) -> set[str]:
    tokens = token_set(value)
    return {
        token
        for token in tokens
        if token not in _STOPWORDS and token not in _COMPARATOR_TOKENS and not _NUMBER_RE.fullmatch(token)
    }


def compare_text(left: str, right: str) -> TextComparison:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return TextComparison(0.0)
    if left_norm == right_norm:
        return TextComparison(1.0, same_side=True)

    left_canonical = _canonical_text(left)
    right_canonical = _canonical_text(right)
    left_tokens = token_set(left_canonical)
    right_tokens = token_set(right_canonical)
    reasons: list[str] = []
    cap = 1.0

    left_numbers = _numbers(left_canonical)
    right_numbers = _numbers(right_canonical)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        cap = min(cap, 0.30)
        reasons.append("numeric_threshold_mismatch")

    left_polarity = _polarity(left_tokens)
    right_polarity = _polarity(right_tokens)
    opposite_side = bool(left_polarity and right_polarity and left_polarity != right_polarity)
    same_side = bool(left_polarity and right_polarity and left_polarity == right_polarity and left_numbers == right_numbers)
    if opposite_side:
        cap = min(cap, 0.30)
        reasons.append("opposite_polarity_detected")

    seq = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_content = _content_tokens(left_canonical)
    right_content = _content_tokens(right_canonical)
    if left_content and right_content:
        jaccard = len(left_content & right_content) / len(left_content | right_content)
    else:
        jaccard = 0.0
    score = (0.55 * seq) + (0.45 * jaccard)
    return TextComparison(round(min(score, cap), 4), tuple(reasons), same_side=same_side, opposite_side=opposite_side)


def _similarity(left: str, right: str) -> float:
    return compare_text(left, right).score


def side_terms_compatible(left: str, right: str) -> TextComparison:
    comparison = compare_text(left, right)
    left_numbers = _numbers(_canonical_text(left))
    right_numbers = _numbers(_canonical_text(right))
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return comparison
    if comparison.opposite_side or comparison.same_side:
        return comparison
    if normalize_text(left) == normalize_text(right):
        return TextComparison(comparison.score, comparison.reasons, same_side=True)
    return comparison


def _rule_key_tokens(value: str) -> set[str]:
    return {token for token in _content_tokens(value) if len(token) >= 3}


def _settlement_rules_compatible(left_rule: str, right_rule: str) -> bool:
    left_tokens = _rule_key_tokens(left_rule)
    right_tokens = _rule_key_tokens(right_rule)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    return len(left_tokens & right_tokens) >= 2


def assess_match(
    left: NormalizedMarket,
    right: NormalizedMarket,
    config: ScannerConfig | None = None,
) -> MatchAssessment:
    config = config or ScannerConfig()
    reasons: list[str] = []
    event_comparison = compare_text(left.event_name, right.event_name)
    outcome_comparison = compare_text(left.outcome_name, right.outcome_name)
    event_score = event_comparison.score
    outcome_score = outcome_comparison.score
    reasons.extend(f"event_{reason}" for reason in event_comparison.reasons)
    reasons.extend(f"outcome_{reason}" for reason in outcome_comparison.reasons)
    confidence = (0.72 * event_score) + (0.28 * outcome_score)
    cap = 1.0
    settlement_mismatch_risk = 0.0

    if event_score < 0.65:
        reasons.append(f"event_name similarity is low ({event_score:.2f})")
    if outcome_score < 0.70:
        reasons.append(f"outcome_name similarity is low ({outcome_score:.2f})")
        cap = min(cap, 0.75)
    if min(event_score, outcome_score) < 0.85:
        cap = min(cap, config.min_possible_arb_confidence - config.confidence_cap_headroom_below_arb)
        reasons.append("confidence_requires_both_event_and_outcome_high")

    if left.settlement_time and right.settlement_time:
        delta_hours = abs((left.settlement_time - right.settlement_time).total_seconds()) / 3600.0
        if delta_hours <= 2:
            reasons.append("settlement times align")
        elif delta_hours <= 24:
            settlement_mismatch_risk = max(settlement_mismatch_risk, 0.18)
            cap = min(cap, 0.80)
            reasons.append(f"settlement times differ by {delta_hours:.1f}h")
        else:
            settlement_mismatch_risk = max(settlement_mismatch_risk, 0.60)
            cap = min(cap, 0.55)
            reasons.append(f"settlement times conflict by {delta_hours:.1f}h")
    else:
        settlement_mismatch_risk = max(settlement_mismatch_risk, 0.25)
        cap = min(cap, 0.75)
        reasons.append("one or both settlement times are missing")

    if not _settlement_rules_compatible(left.settlement_rule, right.settlement_rule):
        settlement_mismatch_risk = max(settlement_mismatch_risk, 0.25)
        cap = min(cap, 0.80)
        reasons.append("side_definition_unverified")

    confidence = min(confidence, cap)
    return MatchAssessment(
        match_confidence=round(confidence, 4),
        settlement_mismatch_risk=round(settlement_mismatch_risk, 4),
        reasons=tuple(reasons),
    )
