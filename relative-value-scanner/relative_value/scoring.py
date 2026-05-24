from __future__ import annotations

from typing import Optional

from relative_value.config import ScannerConfig
from relative_value.matching import assess_match, side_terms_compatible
from relative_value.models import (
    ACTION_SEVERITY,
    Action,
    NormalizedMarket,
    RelativeValueCandidate,
    SourceKind,
)


def _cap_action(action: Action, max_action: Action) -> Action:
    if ACTION_SEVERITY[action] <= ACTION_SEVERITY[max_action]:
        return action
    return max_action


def _both_executable_exchanges(left: NormalizedMarket, right: NormalizedMarket) -> bool:
    return (
        left.source_kind == SourceKind.EXCHANGE
        and right.source_kind == SourceKind.EXCHANGE
        and left.is_executable
        and right.is_executable
    )


def _quote_freshness_cap(
    left: NormalizedMarket,
    right: NormalizedMarket,
    config: ScannerConfig,
) -> tuple[Action | None, tuple[str, ...]]:
    captured_times = [item for item in (left.captured_at, right.captured_at) if item is not None]
    if len(captured_times) < 2:
        return Action.MANUAL_REVIEW, ("quote_freshness_unverified",)
    newest = max(captured_times)
    for captured_at in captured_times:
        # TODO(live-mode): also compare newest to now(); relative-only freshness passes stale-pair-stale-pair.
        age_seconds = (newest - captured_at).total_seconds()
        if age_seconds > config.max_quote_age_seconds:
            return Action.MANUAL_REVIEW, ("stale_quote",)
    return None, ()


def _best_exchange_gap(
    left: NormalizedMarket,
    right: NormalizedMarket,
    config: ScannerConfig,
) -> tuple[Optional[float], Optional[float], str, dict[str, float], tuple[str, ...]]:
    options: list[tuple[float, str, dict[str, float], tuple[str, ...]]] = []
    if left.yes_ask is not None and right.yes_bid is not None:
        left_fee = config.fee_model.fee_for_leg(left.yes_ask)
        right_no_price = 1.0 - right.yes_bid
        right_fee = config.fee_model.fee_for_leg(right_no_price)
        fees = {
            f"{left.venue}:{left.market_id}:buy_yes": left_fee,
            f"{right.venue}:{right.market_id}:buy_no_assumed": right_fee,
        }
        gross_gap = right.yes_bid - left.yes_ask
        fee_adjusted_gap = gross_gap - left_fee - right_fee - config.no_side_spread_penalty
        options.append(
            (
                gross_gap,
                f"buy YES on {left.venue}, offset via assumed NO on {right.venue}",
                fees,
                ("no_side_spread_assumed", f"fee_adjusted_gap={fee_adjusted_gap:.4f}"),
            )
        )
    if right.yes_ask is not None and left.yes_bid is not None:
        right_fee = config.fee_model.fee_for_leg(right.yes_ask)
        left_no_price = 1.0 - left.yes_bid
        left_fee = config.fee_model.fee_for_leg(left_no_price)
        fees = {
            f"{right.venue}:{right.market_id}:buy_yes": right_fee,
            f"{left.venue}:{left.market_id}:buy_no_assumed": left_fee,
        }
        gross_gap = left.yes_bid - right.yes_ask
        fee_adjusted_gap = gross_gap - right_fee - left_fee - config.no_side_spread_penalty
        options.append(
            (
                gross_gap,
                f"buy YES on {right.venue}, offset via assumed NO on {left.venue}",
                fees,
                ("no_side_spread_assumed", f"fee_adjusted_gap={fee_adjusted_gap:.4f}"),
            )
        )
    if not options:
        return None, None, "missing executable bid/ask", {}, ()
    gross_gap, direction, fees, reasons = max(options, key=lambda item: item[0])
    fee_adjusted_gap = gross_gap - sum(fees.values()) - config.no_side_spread_penalty
    return gross_gap, fee_adjusted_gap, direction, fees, reasons


def _reference_gap(left: NormalizedMarket, right: NormalizedMarket) -> tuple[Optional[float], tuple[str, ...]]:
    reasons: list[str] = []
    if left.source_kind == SourceKind.SPORTSBOOK_REFERENCE and right.source_kind == SourceKind.SPORTSBOOK_REFERENCE:
        return None, ("both_sides_sportsbook_reference",)
    if left.source_kind == SourceKind.SPORTSBOOK_REFERENCE and right.midpoint is not None:
        if left.yes_reference_probability is None:
            return None, ()
        comparison = side_terms_compatible(left.outcome_name, right.outcome_name)
        if comparison.same_side:
            return abs(left.yes_reference_probability - right.midpoint), ()
        if comparison.opposite_side:
            reasons.append("opposite_reference_outcome_inverted")
            return abs((1.0 - left.yes_reference_probability) - right.midpoint), tuple(reasons)
        reasons.append("reference_side_unconfirmed")
        return None, tuple(reasons)
    if right.source_kind == SourceKind.SPORTSBOOK_REFERENCE and left.midpoint is not None:
        if right.yes_reference_probability is None:
            return None, ()
        comparison = side_terms_compatible(right.outcome_name, left.outcome_name)
        if comparison.same_side:
            return abs(right.yes_reference_probability - left.midpoint), ()
        if comparison.opposite_side:
            reasons.append("opposite_reference_outcome_inverted")
            return abs((1.0 - right.yes_reference_probability) - left.midpoint), tuple(reasons)
        reasons.append("reference_side_unconfirmed")
        return None, tuple(reasons)
    return None, ()


def score_pair(
    left: NormalizedMarket,
    right: NormalizedMarket,
    config: ScannerConfig | None = None,
) -> RelativeValueCandidate:
    config = config or ScannerConfig()
    match = assess_match(left, right, config)
    reasons = list(match.reasons)
    gross_gap: Optional[float] = None
    fee_adjusted_gap: Optional[float] = None
    fees_applied: dict[str, float] = {}
    reference_gap, reference_reasons = _reference_gap(left, right)
    reasons.extend(reference_reasons)
    freshness_cap, freshness_reasons = _quote_freshness_cap(left, right, config)
    reasons.extend(freshness_reasons)
    limiting_liquidity_top_contracts = min(left.liquidity_top_contracts, right.liquidity_top_contracts)
    direction = f"reference comparison {left.venue} vs {right.venue}"
    if "opposite_reference_outcome_inverted" in reference_reasons:
        direction += " (opposite outcome inverted)"
    if "both_sides_sportsbook_reference" in reference_reasons:
        direction += " (both sportsbook references)"

    if match.match_confidence < config.min_watch_confidence:
        action = Action.IGNORE
        reasons.append("match confidence below watch threshold")
    elif not _both_executable_exchanges(left, right):
        if reference_gap is not None and reference_gap >= config.reference_gap_manual_review and match.match_confidence >= config.min_manual_review_confidence:
            action = Action.MANUAL_REVIEW
            reasons.append("large reference gap, but reference odds are not executable")
        elif reference_gap is not None and reference_gap >= config.reference_gap_watch:
            action = Action.WATCH
            reasons.append("reference gap is watchable, but not executable")
        else:
            action = Action.WATCH if match.match_confidence >= config.min_manual_review_confidence else Action.IGNORE
            reasons.append("not an exchange-vs-exchange executable pair")
    else:
        gross_gap, fee_adjusted_gap, direction, fees_applied, gap_reasons = _best_exchange_gap(left, right, config)
        reasons.extend(gap_reasons)
        possible_arb_gates = (
            match.match_confidence >= config.min_possible_arb_confidence
            and match.settlement_mismatch_risk <= config.max_possible_arb_mismatch_risk
            and fee_adjusted_gap is not None
            and fee_adjusted_gap >= config.min_possible_arb_fee_adjusted_gap
            and limiting_liquidity_top_contracts >= config.min_liquidity_top_contracts
        )
        if possible_arb_gates:
            action = Action.POSSIBLE_ARB
            reasons.append("all possible-arb hard gates passed")
        elif (
            fee_adjusted_gap is not None
            and fee_adjusted_gap >= config.min_paper_fee_adjusted_gap
            and match.match_confidence >= config.min_paper_confidence
            and match.settlement_mismatch_risk <= config.max_paper_mismatch_risk
            and limiting_liquidity_top_contracts >= config.min_liquidity_top_contracts
        ):
            action = Action.PAPER
            reasons.append("positive fee-adjusted gap, but possible-arb gates not met")
        elif gross_gap is not None and gross_gap > 0:
            action = Action.MANUAL_REVIEW
            reasons.append("positive gross gap needs manual review")
        else:
            action = Action.WATCH
            reasons.append("matched executable markets without positive gap")

    if match.match_confidence < config.min_paper_confidence:
        action = _cap_action(action, Action.MANUAL_REVIEW)
        reasons.append("confidence caps action below PAPER")
    if match.settlement_mismatch_risk > config.max_possible_arb_mismatch_risk:
        action = _cap_action(action, Action.MANUAL_REVIEW)
        reasons.append("settlement mismatch risk blocks POSSIBLE_ARB")
    if match.settlement_mismatch_risk >= 0.20:
        action = _cap_action(action, Action.WATCH)
        reasons.append("high settlement mismatch risk caps action at WATCH")
    if left.source_kind == SourceKind.SPORTSBOOK_REFERENCE or right.source_kind == SourceKind.SPORTSBOOK_REFERENCE:
        action = _cap_action(action, Action.MANUAL_REVIEW)
        reasons.append("sportsbook odds are reference-only")
    if freshness_cap is not None:
        action = _cap_action(action, freshness_cap)

    return RelativeValueCandidate(
        left=left,
        right=right,
        match=match,
        action=action,
        gross_gap=round(gross_gap, 6) if gross_gap is not None else None,
        fee_adjusted_gap=round(fee_adjusted_gap, 6) if fee_adjusted_gap is not None else None,
        reference_gap=round(reference_gap, 6) if reference_gap is not None else None,
        limiting_liquidity_top_contracts=limiting_liquidity_top_contracts,
        direction=direction,
        fees_applied=fees_applied,
        reasons=tuple(reasons),
    )
