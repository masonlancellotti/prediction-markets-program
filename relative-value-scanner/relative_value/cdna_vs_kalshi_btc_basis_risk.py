from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "cdna_vs_kalshi_btc_basis_risk_v1"
CDNA_SOURCE = "crypto_com_predict_cdna_research_snapshot_v1"
STANDARDIZED_SOURCE = "standardized_family_candidates_v1"

BTC_BASIS_RISK_REVIEW = "BTC_BASIS_RISK_REVIEW"
MANUAL_BASIS_RISK_REVIEW = "MANUAL_BASIS_RISK_REVIEW"
CDNA_SOURCE_INDEX = "CDNA U-BTC midpoint (Lukka/ICE/Blockstream)"
KALSHI_BRTI_SOURCE = "CF Benchmarks / BRTI"


def build_cdna_vs_kalshi_btc_basis_risk_report(
    *,
    cdna_path: Path,
    standardized_path: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    cdna_payload, cdna_warning = _load_json(cdna_path, expected_source=CDNA_SOURCE)
    standardized_payload, standardized_warning = _load_json(standardized_path, expected_source=STANDARDIZED_SOURCE)
    warnings = [warning for warning in (cdna_warning, standardized_warning) if warning is not None]
    cdna_rows = _cdna_btc_rows(cdna_payload)
    kalshi_rows = _kalshi_btc_rows(standardized_payload)

    rows: list[dict[str, Any]] = []
    mismatch_counts: Counter[str] = Counter()
    for cdna in cdna_rows:
        for kalshi in kalshi_rows:
            mismatch = _mismatch_reason(cdna, kalshi)
            if mismatch is not None:
                mismatch_counts[mismatch] += 1
                continue
            rows.append(_basis_risk_row(cdna, kalshi))

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "cdna_path": str(cdna_path),
        "standardized_path": str(standardized_path),
        "summary": _summary(
            rows,
            cdna_rows=cdna_rows,
            kalshi_rows=kalshi_rows,
            mismatch_counts=mismatch_counts,
            warnings=warnings,
        ),
        "rows": rows,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "exact_payoff_claimed": False,
            "treats_cdna_and_kalshi_btc_as_exact_same_payoff": False,
        },
    }


def write_cdna_vs_kalshi_btc_basis_risk_file(
    *,
    cdna_path: Path,
    standardized_path: Path,
    json_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna_path,
        standardized_path=standardized_path,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _basis_risk_row(cdna: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    source_a = _string_or_none(cdna.get("price_source_index")) or CDNA_SOURCE_INDEX
    source_b = _kalshi_source_label(kalshi.get("price_source_index"))
    window_a = _string_or_none(cdna.get("settlement_window")) or "unknown"
    window_b = _string_or_none(kalshi.get("settlement_window")) or "unknown"
    return {
        "relationship_class": BTC_BASIS_RISK_REVIEW,
        "family": "CRYPTO_PRICE_THRESHOLD",
        "typed_key": {
            "asset": "BTC",
            "measurement_date": cdna.get("measurement_date"),
            "measurement_time": cdna.get("measurement_time"),
            "threshold_operator": _normalize_operator(cdna.get("threshold_operator")),
            "threshold_value": _number_or_none(cdna.get("threshold_value")),
        },
        "source_a": source_a,
        "source_b": source_b,
        "window_a": window_a,
        "window_b": window_b,
        "source_pair_known_reputable": True,
        "basis_risk_severity_hint": "moderate_known_different_sources_same_window",
        "basis_risk_reason": "same_btc_threshold_operator_date_time_with_known_different_reputable_sources",
        "not_exact_payoff_reason": (
            "CDNA U-BTC midpoint methodology differs from Kalshi CF Benchmarks/BRTI settlement; "
            "basis-risk/fair-value review only."
        ),
        "allowed_next_action": MANUAL_BASIS_RISK_REVIEW,
        "blockers": [
            "different_settlement_source",
            "basis_risk_not_exact_same_payoff",
            "cdna_research_only_not_executable",
        ],
        "market_a": _cdna_market_ref(cdna),
        "market_b": _kalshi_market_ref(kalshi),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "paper_candidate_emitted": False,
        "exact_payoff_claimed": False,
    }


def _mismatch_reason(cdna: dict[str, Any], kalshi: dict[str, Any]) -> str | None:
    if str(cdna.get("asset") or "").upper() != "BTC" or str(kalshi.get("asset") or "").upper() != "BTC":
        return "asset_mismatch"
    if _date_key(cdna.get("measurement_date")) != _date_key(kalshi.get("measurement_date")):
        return "measurement_date_mismatch"
    if _normalize_operator(cdna.get("threshold_operator")) != _normalize_operator(kalshi.get("threshold_operator")):
        return "threshold_operator_mismatch"
    if not _same_number(cdna.get("threshold_value"), kalshi.get("threshold_value")):
        return "threshold_value_mismatch"
    if _time_key(cdna.get("measurement_time")) != _time_key(kalshi.get("measurement_time")):
        return "measurement_time_mismatch"
    if not _compatible_window(cdna.get("settlement_window"), kalshi.get("settlement_window")):
        return "settlement_window_mismatch"
    if not _known_cdna_source(cdna.get("price_source_index")):
        return "cdna_source_not_known_reputable"
    if not _known_kalshi_source(kalshi.get("price_source_index")):
        return "kalshi_source_not_known_reputable"
    return None


def _cdna_btc_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("venue") == "crypto_com_predict_cdna"
        and str(row.get("asset") or "").upper() == "BTC"
        and row.get("basis_risk_compatible_with_kalshi") is True
    ]


def _kalshi_btc_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    kalshi_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("family") != "CRYPTO_PRICE_THRESHOLD":
            continue
        typed_key = row.get("typed_key") if isinstance(row.get("typed_key"), dict) else {}
        if str(typed_key.get("asset") or "").upper() != "BTC":
            continue
        for market in row.get("markets") or []:
            if not isinstance(market, dict) or market.get("venue") != "kalshi":
                continue
            kalshi_rows.append(
                {
                    "asset": "BTC",
                    "threshold_value": typed_key.get("threshold_value"),
                    "threshold_operator": typed_key.get("threshold_operator"),
                    "measurement_date": typed_key.get("measurement_date"),
                    "measurement_time": typed_key.get("timestamp"),
                    "settlement_window": typed_key.get("settlement_window"),
                    "price_source_index": typed_key.get("price_source_index"),
                    "market": market,
                }
            )
    return kalshi_rows


def _cdna_market_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "venue": row.get("venue"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "title": row.get("title"),
        "permission": row.get("permission"),
        "raw_source_file": row.get("raw_source_file"),
        "raw_row_index": row.get("raw_row_index"),
        "execution_allowed_in_project_now": row.get("execution_allowed_in_project_now"),
        "can_create_paper_candidate": row.get("can_create_paper_candidate"),
    }


def _kalshi_market_ref(row: dict[str, Any]) -> dict[str, Any]:
    market = row.get("market") if isinstance(row.get("market"), dict) else {}
    return {
        "venue": market.get("venue"),
        "event_id": market.get("event_id"),
        "event_ticker": market.get("event_ticker"),
        "market_id": market.get("market_id"),
        "ticker": market.get("ticker"),
        "review_readiness_tier": market.get("review_readiness_tier"),
        "source_file": market.get("source_file"),
        "row_index": market.get("row_index"),
    }


def _summary(
    rows: list[dict[str, Any]],
    *,
    cdna_rows: list[dict[str, Any]],
    kalshi_rows: list[dict[str, Any]],
    mismatch_counts: Counter[str],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = Counter(blocker for row in rows for blocker in row.get("blockers") or [])
    return {
        "cdna_btc_rows_considered": len(cdna_rows),
        "kalshi_btc_rows_considered": len(kalshi_rows),
        "basis_risk_row_count": len(rows),
        "btc_basis_risk_review_count": sum(1 for row in rows if row.get("relationship_class") == BTC_BASIS_RISK_REVIEW),
        "paper_candidate_count": 0,
        "exact_payoff_claimed_count": 0,
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(10)],
        "warning_count": len(warnings),
    }


def _load_json(path: Path, *, expected_source: str) -> tuple[Any, dict[str, Any] | None]:
    if not path.exists():
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}
    if not isinstance(payload, dict) or payload.get("source") != expected_source:
        return payload, {
            "source_file": str(path),
            "reason_code": "unexpected_report_source",
            "blocker": f"expected_{expected_source}",
        }
    return payload, None


def _kalshi_source_label(value: Any) -> str:
    text = _normalized_source(value)
    if "cf benchmarks" in text or "brti" in text or "bitcoin real time index" in text:
        return KALSHI_BRTI_SOURCE
    return _string_or_none(value) or "unknown"


def _known_cdna_source(value: Any) -> bool:
    text = _normalized_source(value)
    return bool(text and all(token in text for token in ("cdna", "btc")) and any(token in text for token in ("lukka", "ice", "blockstream")))


def _known_kalshi_source(value: Any) -> bool:
    text = _normalized_source(value)
    return bool(text and ("cf benchmarks" in text or "brti" in text or "bitcoin real time index" in text))


def _compatible_window(left: Any, right: Any) -> bool:
    return _window_class(left) == "60_second_preceding" and _window_class(right) == "60_second_preceding"


def _window_class(value: Any) -> str:
    text = _normalized_source(value)
    if "60" in text and ("preceding" in text or "minute" in text or "second" in text):
        return "60_second_preceding"
    return text or "unknown"


def _date_key(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    cleaned = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned.replace(",", " ")).strip()
    for fmt in ("%b %d %Y", "%B %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return cleaned.lower()


def _time_key(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    normalized = text.upper().replace(".", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = normalized.replace("ET", "EDT")
    return normalized


def _normalize_operator(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    token = text.lower().replace(" ", "_")
    if token in {"above", "over", "greater_than", ">"}:
        return ">"
    if token in {"below", "under", "less_than", "<"}:
        return "<"
    if token in {"at_least", ">="}:
        return ">="
    if token in {"at_most", "<="}:
        return "<="
    return token


def _same_number(left: Any, right: Any) -> bool:
    left_number = _number_or_none(left)
    right_number = _number_or_none(right)
    if left_number is None or right_number is None:
        return False
    return abs(left_number - right_number) < 0.000001


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_source(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
