from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value._numeric import float_or_none
from relative_value.contract_relationship import classify_contract_relationship, report_blocking_reasons
from relative_value.fees import FeeModel, KalshiTieredFeeModel, NoFeeModel
from relative_value.same_payoff_evidence import SAME_PAYOFF_BOARD_CLASSIFIER_VERSION, SAME_PAYOFF_BOARD_SOURCE


SUPPORTED_SCHEMA_VERSION = 1
ACTION_WATCH = "WATCH"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_PAPER_CANDIDATE = "PAPER_CANDIDATE"
UNIT_WARNING = "polymarket_shares_vs_kalshi_contracts_not_normalized"
DISCLAIMER = (
    "Read-only paper candidate ledger. This is not trading advice, not an order, "
    "not a profit claim, and never emits PAPER or POSSIBLE_ARB."
)
ALLOWED_SAME_PAYOFF_RELATIONSHIP_SOURCES = {SAME_PAYOFF_BOARD_SOURCE}


@dataclass(frozen=True)
class PaperCandidateEvaluatorConfig:
    max_quote_age_seconds: float = 60.0
    max_settlement_delta_seconds: float = 3600.0
    min_top_of_book_size: float = 1.0
    min_net_gap: float = 0.01
    accept_unit_mismatch: bool = False
    polymarket_fee_model: FeeModel = field(default_factory=NoFeeModel)
    kalshi_fee_model: FeeModel = field(default_factory=KalshiTieredFeeModel)


def evaluate_paper_candidate_files(
    *,
    pairs_path: Path,
    polymarket_enriched_path: Path,
    kalshi_enriched_path: Path,
    output_path: Path | None = None,
    config: PaperCandidateEvaluatorConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    detected_at = now or datetime.now(timezone.utc)
    _require_tz_aware(detected_at, "now")
    pairs_payload = _load_json_object(pairs_path, "pairs")
    polymarket_payload = _load_json_object(polymarket_enriched_path, "polymarket_enriched")
    kalshi_payload = _load_json_object(kalshi_enriched_path, "kalshi_enriched")
    payload = evaluate_paper_candidates(
        pairs_payload=pairs_payload,
        polymarket_payload=polymarket_payload,
        kalshi_payload=kalshi_payload,
        inputs={
            "pairs": str(pairs_path),
            "polymarket_enriched": str(polymarket_enriched_path),
            "kalshi_enriched": str(kalshi_enriched_path),
        },
        config=config,
        detected_at=detected_at,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def evaluate_paper_candidates(
    *,
    pairs_payload: dict[str, Any],
    polymarket_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    inputs: dict[str, str] | None = None,
    config: PaperCandidateEvaluatorConfig | None = None,
    detected_at: datetime | None = None,
) -> dict[str, Any]:
    cfg = config or PaperCandidateEvaluatorConfig()
    generated_at = detected_at or datetime.now(timezone.utc)
    _require_tz_aware(generated_at, "detected_at")

    pairs_data = copy.deepcopy(pairs_payload)
    polymarket_data = copy.deepcopy(polymarket_payload)
    kalshi_data = copy.deepcopy(kalshi_payload)
    _validate_schema_one("pairs", pairs_data)
    _validate_schema_one("polymarket_enriched", polymarket_data)
    _validate_schema_one("kalshi_enriched", kalshi_data)

    pairs = pairs_data.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain a pairs list")
    polymarket_rows = _market_rows(polymarket_data, "polymarket_enriched")
    kalshi_rows = _market_rows(kalshi_data, "kalshi_enriched")
    polymarket_by_id = {_string_or_empty(row.get("market_id")): row for row in polymarket_rows if _string_or_empty(row.get("market_id"))}
    kalshi_by_ticker = {
        _string_or_empty(row.get("ticker") or row.get("market_id")): row
        for row in kalshi_rows
        if _string_or_empty(row.get("ticker") or row.get("market_id"))
    }

    ledger: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        poly_id = _pair_polymarket_id(pair)
        kalshi_ticker = _pair_kalshi_ticker(pair)
        polymarket = polymarket_by_id.get(poly_id)
        kalshi = kalshi_by_ticker.get(kalshi_ticker)
        ledger.append(_evaluate_pair(pair, polymarket, kalshi, cfg, generated_at, poly_id, kalshi_ticker))

    counts = {ACTION_WATCH: 0, ACTION_MANUAL_REVIEW: 0, ACTION_PAPER_CANDIDATE: 0}
    for row in ledger:
        counts[row["action"]] += 1
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "generated_at": generated_at.isoformat(),
        "inputs": inputs or {
            "pairs": "<in-memory>",
            "polymarket_enriched": "<in-memory>",
            "kalshi_enriched": "<in-memory>",
        },
        "ledger_count": len(ledger),
        "ledger": ledger,
        "counts_by_action": counts,
        "disclaimer": DISCLAIMER,
    }


def _evaluate_pair(
    pair: dict[str, Any],
    polymarket: dict[str, Any] | None,
    kalshi: dict[str, Any] | None,
    cfg: PaperCandidateEvaluatorConfig,
    detected_at: datetime,
    poly_id: str,
    kalshi_ticker: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    if polymarket is None or kalshi is None:
        if polymarket is None:
            reasons.append("missing_polymarket_enriched_market")
        if kalshi is None:
            reasons.append("missing_kalshi_enriched_market")
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", reasons, "missing_enriched_join")

    poly_enrichment = _enrichment(polymarket)
    kalshi_enrichment = _enrichment(kalshi)
    if poly_enrichment.get("enrichment_status") != "enriched" or kalshi_enrichment.get("enrichment_status") != "enriched":
        if poly_enrichment.get("enrichment_status") != "enriched":
            reasons.append("polymarket_orderbook_not_enriched")
            reasons.extend(_prefixed_enrichment_warnings("polymarket", poly_enrichment))
        if kalshi_enrichment.get("enrichment_status") != "enriched":
            reasons.append("kalshi_orderbook_not_enriched")
            reasons.extend(_prefixed_enrichment_warnings("kalshi", kalshi_enrichment))
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", reasons, "orderbook_not_enriched")

    prices = _prices(poly_enrichment, kalshi_enrichment)
    if any(value is None for value in prices.values()):
        for key, value in prices.items():
            if value is None:
                reasons.append(f"{key}_missing")
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", reasons, "missing_best_bid_or_ask")

    poly_captured = _parse_datetime_or_none(poly_enrichment.get("orderbook_captured_at"))
    kalshi_captured = _parse_datetime_or_none(kalshi_enrichment.get("orderbook_captured_at"))
    quote_time_reason = _quote_time_reason("polymarket", poly_captured, detected_at, cfg.max_quote_age_seconds)
    if quote_time_reason:
        reasons.append(quote_time_reason)
    quote_time_reason = _quote_time_reason("kalshi", kalshi_captured, detected_at, cfg.max_quote_age_seconds)
    if quote_time_reason:
        reasons.append(quote_time_reason)
    if reasons:
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", reasons, "stale_or_missing_quote_time")

    direction = _best_direction(prices)
    depth_reasons = _depth_reasons(direction, poly_enrichment, kalshi_enrichment, cfg.min_top_of_book_size)
    if depth_reasons:
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", depth_reasons, "insufficient_top_of_book_depth", direction)

    settlement = _settlement_status(polymarket, kalshi, cfg.max_settlement_delta_seconds)
    settlement_direction = {**direction, "settlement_delta_seconds": settlement.delta_seconds}
    if settlement.reason and settlement.manual_ceiling:
        return _ledger_row(
            pair,
            polymarket,
            kalshi,
            detected_at,
            ACTION_MANUAL_REVIEW,
            "near_equivalent_manual_review",
            [settlement.reason],
            settlement.reason,
            settlement_direction,
        )
    if settlement.reason:
        return _ledger_row(
            pair,
            polymarket,
            kalshi,
            detected_at,
            ACTION_WATCH,
            "ineligible",
            [settlement.reason],
            settlement.reason,
            settlement_direction,
        )

    direction = settlement_direction
    matcher_reasons = _matcher_reasons(pair)
    if matcher_reasons:
        if set(matcher_reasons) == {"ambiguous_wording"}:
            return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_MANUAL_REVIEW, "near_equivalent_manual_review", matcher_reasons, "ambiguous_wording", direction)
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", matcher_reasons, "matcher_ineligibility_reason", direction)

    if _venue(polymarket) != "polymarket" or _venue(kalshi) != "kalshi":
        return _ledger_row(
            pair,
            polymarket,
            kalshi,
            detected_at,
            ACTION_WATCH,
            "reference_only_watch",
            ["sportsbook_or_reference_side_not_executable"],
            "reference_only_watch",
            direction,
        )

    if direction["gross_gap"] <= 0:
        return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_WATCH, "ineligible", ["no_positive_bid_ask_gap"], "no_positive_bid_ask_gap", direction)

    fees = _fees(direction, cfg.polymarket_fee_model, cfg.kalshi_fee_model)
    direction.update(fees)
    if direction["estimated_net_gap"] < cfg.min_net_gap:
        return _ledger_row(
            pair,
            polymarket,
            kalshi,
            detected_at,
            ACTION_WATCH,
            "ineligible",
            ["estimated_net_gap_below_minimum"],
            "estimated_net_gap_below_minimum",
            direction,
        )

    if not cfg.accept_unit_mismatch:
        return _ledger_row(
            pair,
            polymarket,
            kalshi,
            detected_at,
            ACTION_MANUAL_REVIEW,
            "near_equivalent_manual_review",
            ["unit_mismatch_not_accepted"],
            "unit_mismatch_not_accepted",
            direction,
        )
    if not _pair_relationship_allows_paper_candidate(pair):
        return _ledger_row(
            pair,
            polymarket,
            kalshi,
            detected_at,
            ACTION_MANUAL_REVIEW,
            "near_equivalent_manual_review",
            ["relationship_same_payoff_not_proven"],
            "relationship_same_payoff_not_proven",
            direction,
        )
    return _ledger_row(pair, polymarket, kalshi, detected_at, ACTION_PAPER_CANDIDATE, "strict_cross_venue_equivalent", [], None, direction)


@dataclass(frozen=True)
class _SettlementStatus:
    reason: str | None
    manual_ceiling: bool = False
    delta_seconds: float | None = None


def _settlement_status(polymarket: dict[str, Any], kalshi: dict[str, Any], max_delta_seconds: float) -> _SettlementStatus:
    poly_value = _settlement_time_value(polymarket)
    kalshi_value = _settlement_time_value(kalshi)
    poly_dt = _parse_datetime_or_none(poly_value)
    kalshi_dt = _parse_datetime_or_none(kalshi_value)
    if poly_dt is None or kalshi_dt is None:
        return _SettlementStatus("settlement_time_missing_or_naive", manual_ceiling=True)
    delta = abs((poly_dt - kalshi_dt).total_seconds())
    if delta > max_delta_seconds:
        return _SettlementStatus("settlement_delta_exceeds_limit", delta_seconds=delta)
    return _SettlementStatus(None, delta_seconds=delta)


def _settlement_time_value(market: dict[str, Any]) -> str | None:
    end_date = _string_or_none(market.get("end_date"))
    if end_date is not None:
        return end_date
    return _string_or_none(market.get("close_time"))


def _best_direction(prices: dict[str, float | None]) -> dict[str, Any]:
    poly_bid = float(prices["polymarket_best_bid"])
    poly_ask = float(prices["polymarket_best_ask"])
    kalshi_bid = float(prices["kalshi_best_bid"])
    kalshi_ask = float(prices["kalshi_best_ask"])
    sell_poly_gap = poly_bid - kalshi_ask
    sell_kalshi_gap = kalshi_bid - poly_ask
    if sell_poly_gap >= sell_kalshi_gap:
        return {
            "buy_venue": "kalshi",
            "sell_venue": "polymarket",
            "gross_gap": round(sell_poly_gap, 6),
            "polymarket_would_enter_side": "SELL_YES",
            "polymarket_would_enter_price": poly_bid,
            "kalshi_would_enter_side": "BUY_YES",
            "kalshi_would_enter_price": kalshi_ask,
        }
    return {
        "buy_venue": "polymarket",
        "sell_venue": "kalshi",
        "gross_gap": round(sell_kalshi_gap, 6),
        "polymarket_would_enter_side": "BUY_YES",
        "polymarket_would_enter_price": poly_ask,
        "kalshi_would_enter_side": "SELL_YES",
        "kalshi_would_enter_price": kalshi_bid,
    }


def _depth_reasons(
    direction: dict[str, Any],
    polymarket_enrichment: dict[str, Any],
    kalshi_enrichment: dict[str, Any],
    min_top_of_book_size: float,
) -> list[str]:
    reasons: list[str] = []
    for venue, enrichment in (("polymarket", polymarket_enrichment), ("kalshi", kalshi_enrichment)):
        side = direction[f"{venue}_would_enter_side"]
        depth_key = "depth_at_best_ask" if side == "BUY_YES" else "depth_at_best_bid"
        depth = float_or_none(enrichment.get(depth_key))
        direction[f"{venue}_would_enter_size"] = depth
        if depth is None:
            reasons.append(f"{venue}_missing_top_of_book_depth")
        elif depth < min_top_of_book_size:
            reasons.append(f"{venue}_insufficient_top_of_book_depth")
    return reasons


def _fees(direction: dict[str, Any], polymarket_fee_model: FeeModel, kalshi_fee_model: FeeModel) -> dict[str, float]:
    polymarket_fee = polymarket_fee_model.fee_for_leg(float(direction["polymarket_would_enter_price"]))
    kalshi_fee = kalshi_fee_model.fee_for_leg(float(direction["kalshi_would_enter_price"]))
    estimated_net_gap = float(direction["gross_gap"]) - polymarket_fee - kalshi_fee
    return {
        "polymarket_fee": round(polymarket_fee, 6),
        "kalshi_fee": round(kalshi_fee, 6),
        "estimated_net_gap": round(estimated_net_gap, 6),
    }


def _ledger_row(
    pair: dict[str, Any],
    polymarket: dict[str, Any] | None,
    kalshi: dict[str, Any] | None,
    detected_at: datetime,
    action: str,
    opportunity_class: str,
    reasons: list[str],
    missed_fill_reason: str | None,
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    direction = direction or {}
    poly = polymarket or {}
    kal = kalshi or {}
    poly_enrichment = _enrichment(poly)
    kalshi_enrichment = _enrichment(kal)
    return {
        "schema_version": 1,
        "candidate_id": _candidate_id(pair, poly, kal),
        "detected_at": detected_at.isoformat(),
        "action": action,
        "opportunity_class": opportunity_class,
        "polymarket": _venue_row("polymarket", poly, poly_enrichment, direction),
        "kalshi": _venue_row("kalshi", kal, kalshi_enrichment, direction),
        "gap": {
            "gross_gap": direction.get("gross_gap"),
            "polymarket_fee": direction.get("polymarket_fee"),
            "kalshi_fee": direction.get("kalshi_fee"),
            "estimated_net_gap": direction.get("estimated_net_gap"),
            "settlement_delta_seconds": direction.get("settlement_delta_seconds"),
            "size_unit_warning": UNIT_WARNING,
        },
        "ineligibility_reasons": sorted(set(reasons)),
        "contract_relationship": _contract_relationship_row(pair, reasons, missed_fill_reason),
        "missed_fill_reason": missed_fill_reason,
        "markouts": _empty_markouts(),
        "disclaimer": DISCLAIMER,
    }


def _contract_relationship_row(pair: dict[str, Any], reasons: list[str], missed_fill_reason: str | None) -> dict[str, Any]:
    if missed_fill_reason is None and _pair_relationship_allows_paper_candidate(pair):
        return dict(pair["contract_relationship"])
    relationship_reasons = report_blocking_reasons(pair.get("contract_relationship"))
    relationship_reasons.extend(reasons)
    unit_mismatch_reason = UNIT_WARNING if _unit_warning_is_relationship_relevant(missed_fill_reason, relationship_reasons) else None
    return classify_contract_relationship(
        relationship_reasons,
        unit_mismatch_reason=unit_mismatch_reason,
    ).to_report_dict()


def _pair_relationship_allows_paper_candidate(pair: dict[str, Any]) -> bool:
    relationship = pair.get("contract_relationship")
    if not isinstance(relationship, dict):
        return False
    if relationship.get("relationship") != "EQUIVALENT":
        return False
    if relationship.get("same_payoff") is not True:
        return False
    blockers = relationship.get("blocking_reasons")
    if blockers != []:
        return False
    if relationship.get("source") not in ALLOWED_SAME_PAYOFF_RELATIONSHIP_SOURCES:
        return False
    evidence = relationship.get("same_payoff_board_evidence")
    if not isinstance(evidence, dict):
        return False
    if evidence.get("classifier_version") != SAME_PAYOFF_BOARD_CLASSIFIER_VERSION:
        return False
    strict_pass_count = evidence.get("strict_pass_count")
    strict_comparator_count = evidence.get("strict_comparator_count")
    if strict_pass_count != strict_comparator_count:
        return False
    if int(strict_comparator_count or 0) <= 0:
        return False
    return True


def _unit_warning_is_relationship_relevant(missed_fill_reason: str | None, relationship_reasons: list[str]) -> bool:
    if missed_fill_reason is None:
        return True
    if missed_fill_reason in {
        "unit_mismatch_not_accepted",
        "settlement_delta_exceeds_limit",
        "settlement_time_missing_or_naive",
        "ambiguous_wording",
        "matcher_ineligibility_reason",
    }:
        return True
    return bool(
        {
            "sports_competition_scope_mismatch",
            "sports_team_alias_mismatch",
            "different_threshold",
            "different_settlement_source",
        }
        & set(relationship_reasons)
    )


def _venue_row(venue: str, market: dict[str, Any], enrichment: dict[str, Any], direction: dict[str, Any]) -> dict[str, Any]:
    base = {
        "quote_captured_at": enrichment.get("orderbook_captured_at"),
        "best_bid": enrichment.get("best_bid"),
        "best_ask": enrichment.get("best_ask"),
        "depth_at_best_bid": enrichment.get("depth_at_best_bid"),
        "depth_at_best_ask": enrichment.get("depth_at_best_ask"),
        "would_enter_side": direction.get(f"{venue}_would_enter_side"),
        "would_enter_price": direction.get(f"{venue}_would_enter_price"),
        "would_enter_size": direction.get(f"{venue}_would_enter_size"),
    }
    if venue == "polymarket":
        return {
            "market_id": market.get("market_id"),
            "question": market.get("question") or market.get("title"),
            "yes_token_id": _polymarket_yes_token_id(market),
            **base,
        }
    return {
        "ticker": market.get("ticker") or market.get("market_id"),
        "question": market.get("question") or market.get("title"),
        "yes_token_id": None,
        **base,
    }


def _empty_markouts() -> dict[str, dict[str, None]]:
    subfields = {
        "observed_at": None,
        "polymarket_best_bid": None,
        "polymarket_best_ask": None,
        "kalshi_best_bid": None,
        "kalshi_best_ask": None,
        "gross_gap": None,
        "estimated_net_gap": None,
    }
    return {
        "t_plus_30s": dict(subfields),
        "t_plus_5m": dict(subfields),
        "t_plus_30m": dict(subfields),
        "t_plus_2h": dict(subfields),
    }


def _market_rows(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError(f"{label} input must contain normalized_markets list")
    return [row for row in rows if isinstance(row, dict)]


def _validate_schema_one(label: str, payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(f"{label} schema_version must be 1")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _candidate_id(pair: dict[str, Any], polymarket: dict[str, Any], kalshi: dict[str, Any]) -> str:
    poly_id = _pair_polymarket_id(pair) or _string_or_empty(polymarket.get("market_id")) or "missing-polymarket"
    kalshi_id = _pair_kalshi_ticker(pair) or _string_or_empty(kalshi.get("ticker") or kalshi.get("market_id")) or "missing-kalshi"
    return f"{poly_id}__{kalshi_id}"


def _pair_polymarket_id(pair: dict[str, Any]) -> str:
    return _string_or_empty((pair.get("polymarket") or {}).get("market_id"))


def _pair_kalshi_ticker(pair: dict[str, Any]) -> str:
    kalshi = pair.get("kalshi") or {}
    return _string_or_empty(kalshi.get("ticker") or kalshi.get("market_id"))


def _enrichment(market: dict[str, Any]) -> dict[str, Any]:
    enrichment = market.get("orderbook_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _prices(poly_enrichment: dict[str, Any], kalshi_enrichment: dict[str, Any]) -> dict[str, float | None]:
    return {
        "polymarket_best_bid": float_or_none(poly_enrichment.get("best_bid")),
        "polymarket_best_ask": float_or_none(poly_enrichment.get("best_ask")),
        "kalshi_best_bid": float_or_none(kalshi_enrichment.get("best_bid")),
        "kalshi_best_ask": float_or_none(kalshi_enrichment.get("best_ask")),
    }


def _quote_time_reason(prefix: str, captured_at: datetime | None, now: datetime, max_quote_age_seconds: float) -> str | None:
    if captured_at is None:
        return f"{prefix}_quote_time_missing_or_naive"
    age = (now - captured_at).total_seconds()
    if age < 0:
        return f"{prefix}_quote_time_in_future"
    if age >= max_quote_age_seconds:
        return f"{prefix}_stale_quote"
    return None


def _matcher_reasons(pair: dict[str, Any]) -> list[str]:
    reasons = pair.get("ineligibility_reasons")
    if not isinstance(reasons, list):
        return []
    return sorted({str(reason) for reason in reasons if reason is not None})


def _prefixed_enrichment_warnings(venue: str, enrichment: dict[str, Any]) -> list[str]:
    warnings = enrichment.get("enrichment_warnings")
    if not isinstance(warnings, list):
        return []
    return [f"{venue}_{warning}" for warning in warnings if warning is not None]


def _venue(market: dict[str, Any]) -> str:
    return str(market.get("venue") or "").strip().lower()


def _polymarket_yes_token_id(market: dict[str, Any]) -> str | None:
    raw = market.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    token_ids = _maybe_json_array(raw.get("clobTokenIds") or raw.get("clob_token_ids") or market.get("clobTokenIds"))
    outcomes = market.get("outcomes")
    if not isinstance(token_ids, list) or not isinstance(outcomes, list):
        return _string_or_none(market.get("token_id") or market.get("asset_id"))
    yes_indexes = [
        idx
        for idx, outcome in enumerate(outcomes)
        if isinstance(outcome, dict) and str(outcome.get("name") or "").strip().lower() == "yes"
    ]
    if len(yes_indexes) != 1:
        return None
    yes_index = yes_indexes[0]
    if yes_index >= len(token_ids):
        return None
    return _string_or_none(token_ids[yes_index])


def _maybe_json_array(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return []
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
