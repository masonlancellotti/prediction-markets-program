from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from relative_value.models import RelativeValueCandidate


_REASON_PRIORITY = {
    "opposite_polarity_detected": 0,
    "numeric_threshold_mismatch": 1,
    "opposite_reference_outcome_inverted": 2,
    "stale_quote": 3,
    "quote_freshness_unverified": 3,
    "both_sides_sportsbook_reference": 4,
    "side_definition_unverified": 5,
    "no_side_spread_assumed": 6,
    "confidence_requires_both_event_and_outcome_high": 7,
    "reference_side_unconfirmed": 8,
    "settlement mismatch risk blocks POSSIBLE_ARB": 9,
    "high settlement mismatch risk caps action at WATCH": 10,
    "confidence caps action below PAPER": 11,
    "sportsbook odds are reference-only": 12,
    "event_name similarity is low": 13,
    "outcome_name similarity is low": 14,
    "one or both settlement times are missing": 15,
    "settlement times conflict": 16,
    "settlement times differ": 17,
    "settlement times align": 18,
    "match confidence below watch threshold": 19,
    "large reference gap, but reference odds are not executable": 20,
    "reference gap is watchable, but not executable": 21,
    "not an exchange-vs-exchange executable pair": 22,
    "all possible-arb hard gates passed": 23,
    "positive fee-adjusted gap, but possible-arb gates not met": 24,
    "positive gross gap needs manual review": 25,
    "matched executable markets without positive gap": 26,
    "fee_adjusted_gap=": 27,
}


def _reason_priority(reason: str) -> tuple[int, str]:
    for tag, priority in _REASON_PRIORITY.items():
        if tag in reason:
            return priority, reason
    return 999, reason


def write_json_report(candidates: Sequence[RelativeValueCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(candidates),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_markdown_report(candidates: Sequence[RelativeValueCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Relative Value Candidates",
        "",
        "Read-only offline scan. Sportsbook odds are reference-only and cannot create POSSIBLE_ARB.",
        "",
        "| Action | Left | Left Outcome | Right | Right Outcome | Confidence | Mismatch Risk | Liquidity Top Contracts | Gap | Notes |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for candidate in candidates:
        gap = candidate.fee_adjusted_gap
        if gap is None:
            gap = candidate.reference_gap
        gap_text = "" if gap is None else f"{gap:.3f}"
        notes = "; ".join(sorted(candidate.reasons, key=_reason_priority)[:3]).replace("|", "/")
        lines.append(
            "| {action} | {left} | {left_outcome} | {right} | {right_outcome} | {confidence:.2f} | {risk:.2f} | {liquidity:.2f} | {gap} | {notes} |".format(
                action=candidate.action.value,
                left=f"{candidate.left.venue}:{candidate.left.market_id}",
                left_outcome=candidate.left.outcome_name.replace("|", "/"),
                right=f"{candidate.right.venue}:{candidate.right.market_id}",
                right_outcome=candidate.right.outcome_name.replace("|", "/"),
                confidence=candidate.match.match_confidence,
                risk=candidate.match.settlement_mismatch_risk,
                liquidity=candidate.limiting_liquidity_top_contracts,
                gap=gap_text,
                notes=notes,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
