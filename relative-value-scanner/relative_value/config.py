from __future__ import annotations

from dataclasses import dataclass, field

from relative_value.fees import FeeModel, KalshiTieredFeeModel


@dataclass(frozen=True)
class ScannerConfig:
    min_watch_confidence: float = 0.55
    min_manual_review_confidence: float = 0.70
    min_paper_confidence: float = 0.86
    min_possible_arb_confidence: float = 0.92
    confidence_cap_headroom_below_arb: float = 0.07
    max_possible_arb_mismatch_risk: float = 0.05
    max_paper_mismatch_risk: float = 0.12
    min_possible_arb_fee_adjusted_gap: float = 0.02
    min_paper_fee_adjusted_gap: float = 0.005
    reference_gap_watch: float = 0.03
    reference_gap_manual_review: float = 0.08
    fee_model: FeeModel = field(default_factory=KalshiTieredFeeModel)
    no_side_spread_penalty: float = 0.01
    max_quote_age_seconds: float = 120.0
    min_liquidity_top_contracts: float = 25.0
