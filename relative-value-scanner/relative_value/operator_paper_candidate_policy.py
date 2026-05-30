from __future__ import annotations

from collections import Counter
from typing import Any


ACTION_PAPER = "PAPER_CANDIDATE"
ACTION_WATCH = "WATCH"
ACTION_IGNORE = "IGNORE_BLOCKED"

CLASS_STRICT = "STRICT_EXACT"
CLASS_OPERATOR = "OPERATOR_ACCEPTED_RISK"
CLASS_CDNA = "CDNA_FILL_FIRST"
CLASS_NONE = "NONE"

VALID_OPERATOR_RISK_MODES = ("conservative", "standard", "aggressive")

HARD_BLOCKERS = {
    "missing_quote",
    "missing_ask",
    "missing_complement_quote",
    "stale_quote",
    "stale_or_missing_quote",
    "quote_stale",
    "missing_quote_depth",
    "missing_depth",
    "missing_partner_quote",
    "missing_partner_depth",
    "partner_complement_outcome_unavailable",
    "missing_cdna_display_price",
    "fee_review_required",
    "missing_or_uncertain_fee_model",
    "unsupported_schema",
    "unsupported_crypto_threshold_scope",
    "invalid_or_unsupported_world_series_scope",
    "unsupported_market_scope",
    "missing_platform_peer",
    "title_similarity_only_not_equivalence",
    "target_date_mismatch",
    "target_time_mismatch",
    "timezone_mismatch",
    "threshold_grid_mismatch",
    "no_candidate_rows_generated",
    "no_positive_edge",
    "no_positive_indicative_edge",
    "no_positive_net_edge_after_fees",
    "insufficient_available_notional",
    "incompatible_shape",
    "failed_parse",
    "synthetic_bucket_coverage_incomplete",
    "bucket_family_not_exhaustive",
    "missing_bucket_leg_ask",
    "missing_polymarket_complement_ask",
    "incompatible_contract_family",
    "barrier_vs_terminal_mismatch",
    "no_positive_adjusted_net_edge_after_basis_buffer",
    "reference_start_mismatch",
    "interval_length_mismatch",
    "unknown_payoff_vector",
}

# Settlement-time discipline never relaxes for crypto point-in-time markets.
# basis_risk_review_required / source_index_mismatch / source_mismatch are the
# only labels the operator can opt into via accepted_basis=True.
BASIS_INFO_BLOCKERS = {
    "basis_risk_review_required",
    "source_index_mismatch",
    "source_mismatch",
}

TOP_OF_BOOK_SIZE_CAP_BLOCKERS = {
    "missing_quote_depth",
    "missing_depth",
    "quote_size_unit_review_required",
    "insufficient_available_notional",
    "partial_or_missing_depth",
}

CDNA_INFO_BLOCKERS = {
    "cdna_display_price_only",
    "cdna_executable_size_unverified",
    "cdna_no_orderbook_depth",
    "cdna_no_server_side_quote",
    "cdna_partial_fill_risk",
}


def normalize_operator_risk_mode(value: str | None) -> str:
    text = str(value or "conservative").strip().lower()
    if text not in VALID_OPERATOR_RISK_MODES:
        raise ValueError(f"operator_risk_mode must be one of {VALID_OPERATOR_RISK_MODES}, got {value!r}")
    return text


def apply_operator_candidate_fields(
    row: dict[str, Any],
    *,
    paper_class: str = CLASS_NONE,
    assumptions_accepted: list[str] | None = None,
    candidate_action: str = "",
    make_candidate: bool = False,
    mathematical_strict_exact_arb: bool = False,
) -> dict[str, Any]:
    row["paper_candidate"] = bool(make_candidate)
    row["paper_candidate_class"] = paper_class if make_candidate else CLASS_NONE
    strict = bool(mathematical_strict_exact_arb or (make_candidate and paper_class == CLASS_STRICT))
    row["strict_exact_arb"] = strict
    row["mathematical_strict_exact_arb"] = strict
    row["operator_assumptions_accepted"] = bool(make_candidate and paper_class in {CLASS_OPERATOR, CLASS_CDNA})
    row["assumptions_accepted"] = list(assumptions_accepted or []) if make_candidate else []
    row.setdefault("risk_notes", [])
    row["candidate_action"] = candidate_action if make_candidate else ""
    if make_candidate:
        row["action"] = ACTION_PAPER
    return row


def ensure_candidate_fields(row: dict[str, Any]) -> dict[str, Any]:
    row.setdefault("paper_candidate", False)
    row.setdefault("paper_candidate_class", CLASS_NONE)
    row.setdefault("strict_exact_arb", False)
    row.setdefault("mathematical_strict_exact_arb", bool(row.get("strict_exact_arb")))
    row.setdefault("operator_assumptions_accepted", False)
    row.setdefault("assumptions_accepted", [])
    row.setdefault("risk_notes", [])
    row.setdefault("candidate_action", "")
    return row


def candidate_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    classes = Counter(row.get("paper_candidate_class") for row in rows if row.get("paper_candidate"))
    total = sum(classes.values())
    return {
        "strict_paper_candidate_rows": classes.get(CLASS_STRICT, 0),
        "operator_paper_candidate_rows": classes.get(CLASS_OPERATOR, 0),
        "cdna_fill_first_paper_candidate_rows": classes.get(CLASS_CDNA, 0),
        "total_paper_candidate_rows": total,
    }


def has_hard_blocker(
    blockers: list[str],
    *,
    ignore_cdna_info: bool = False,
    accepted_basis: bool = False,
    accepted_top_of_book_size_cap: bool = False,
) -> bool:
    blocker_set = set(blockers)
    if ignore_cdna_info:
        blocker_set -= CDNA_INFO_BLOCKERS
    if accepted_basis:
        # Source/index basis is operator-accepted. Settlement-time discipline
        # (target_time_mismatch, timezone_mismatch) and threshold/date mismatch
        # are NOT relaxed — those represent different settlement instants and
        # remain hard blockers in every operator mode.
        blocker_set -= BASIS_INFO_BLOCKERS
    if accepted_top_of_book_size_cap:
        blocker_set -= TOP_OF_BOOK_SIZE_CAP_BLOCKERS
    return bool(blocker_set & HARD_BLOCKERS)


def collect_hard_blockers(
    blockers: list[str],
    *,
    ignore_cdna_info: bool = False,
    accepted_basis: bool = False,
    accepted_top_of_book_size_cap: bool = False,
) -> list[str]:
    """Return the subset of blockers that are still hard given the operator opts."""
    blocker_set = set(blockers)
    if ignore_cdna_info:
        blocker_set -= CDNA_INFO_BLOCKERS
    if accepted_basis:
        blocker_set -= BASIS_INFO_BLOCKERS
    if accepted_top_of_book_size_cap:
        blocker_set -= TOP_OF_BOOK_SIZE_CAP_BLOCKERS
    return sorted(blocker_set & HARD_BLOCKERS)
