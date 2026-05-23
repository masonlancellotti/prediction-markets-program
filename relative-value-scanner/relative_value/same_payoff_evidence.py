from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SAME_PAYOFF_BOARD_SOURCE = "same_payoff_board_v1"
SAME_PAYOFF_BOARD_CLASSIFIER_VERSION = "same-payoff-board-v1"


def attach_same_payoff_evidence_files(pairs_path: Path, board_path: Path, output_path: Path) -> dict[str, Any]:
    pairs_payload = _load_json_object(pairs_path, "pairs")
    board_payload = _load_json_object(board_path, "same_payoff_board")
    derived = attach_same_payoff_evidence(
        pairs_payload=pairs_payload,
        board_payload=board_payload,
        inputs={"pairs": str(pairs_path), "board": str(board_path)},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(derived, indent=2, sort_keys=True), encoding="utf-8")
    return derived


def attach_same_payoff_evidence(
    *,
    pairs_payload: dict[str, Any],
    board_payload: dict[str, Any],
    inputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    _validate_schema_one("pairs", pairs_payload)
    _validate_schema_one("same_payoff_board", board_payload)
    pairs = pairs_payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain a pairs list")
    rows = board_payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("same_payoff_board input must contain rows list")

    derived = copy.deepcopy(pairs_payload)
    derived_pairs = derived.get("pairs")
    if not isinstance(derived_pairs, list):
        raise ValueError("pairs input must contain a pairs list")

    board_by_identity, ambiguous_board_identities = _unique_rows_by_identity(rows)
    pair_identity_counts = _identity_counts(pair for pair in derived_pairs if isinstance(pair, dict))

    attached_count = 0
    diagnostic_count = 0
    unmatched_count = 0
    ambiguous_count = 0

    for pair in derived_pairs:
        if not isinstance(pair, dict):
            continue
        identity = _pair_identity(pair)
        row = board_by_identity.get(identity) if identity is not None else None
        ambiguous = identity is None or identity in ambiguous_board_identities or pair_identity_counts.get(identity, 0) != 1
        if row is None or ambiguous:
            if ambiguous:
                ambiguous_count += 1
            else:
                unmatched_count += 1
            continue

        evidence = _evidence_for_row(row, board_payload)
        if _row_clears_for_trusted_relationship(row):
            pair["contract_relationship"] = {
                "relationship": "EQUIVALENT",
                "same_payoff": True,
                "confidence": 0.95,
                "blocking_reasons": [],
                "manual_review_required": False,
                "source": SAME_PAYOFF_BOARD_SOURCE,
                "same_payoff_board_evidence": evidence,
            }
            attached_count += 1
        else:
            relationship = pair.get("contract_relationship")
            if isinstance(relationship, dict):
                relationship["same_payoff_board_evidence"] = {**evidence, "passed": False}
                diagnostic_count += 1

    derived["source"] = f"{_string_or_empty(pairs_payload.get('source'))}_with_same_payoff_board_evidence".strip("_")
    derived["same_payoff_evidence_attachment"] = {
        "schema_version": SCHEMA_VERSION,
        "source": SAME_PAYOFF_BOARD_SOURCE,
        "classifier_version": SAME_PAYOFF_BOARD_CLASSIFIER_VERSION,
        "inputs": inputs or {"pairs": "<in-memory>", "board": "<in-memory>"},
        "board_generated_at": board_payload.get("generated_at"),
        "pair_count": len([pair for pair in derived_pairs if isinstance(pair, dict)]),
        "trusted_relationship_attached_count": attached_count,
        "diagnostic_evidence_attached_count": diagnostic_count,
        "unmatched_pair_count": unmatched_count,
        "ambiguous_identity_count": ambiguous_count,
        "original_pairs_mutated": False,
    }
    return derived


def _row_clears_for_trusted_relationship(row: dict[str, Any]) -> bool:
    if row.get("same_payoff") is not True:
        return False
    if row.get("strict_pass_count") != row.get("strict_comparator_count"):
        return False
    if int(row.get("strict_comparator_count") or 0) <= 0:
        return False
    strict_blockers, strict_missing_fields = _strict_blockers_and_missing(row)
    if strict_blockers:
        return False
    if strict_missing_fields:
        return False
    evidence = row.get("same_payoff_evidence")
    if not isinstance(evidence, dict):
        return False
    reference = evidence.get("reference_only_blocker")
    if not isinstance(reference, dict) or reference.get("status") != "PASS":
        return False
    return True


def _evidence_for_row(row: dict[str, Any], board_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "classifier_version": SAME_PAYOFF_BOARD_CLASSIFIER_VERSION,
        "strict_pass_count": row.get("strict_pass_count"),
        "strict_comparator_count": row.get("strict_comparator_count"),
        "board_generated_at": board_payload.get("generated_at"),
        "board_row_id": _board_row_id(row),
        "evidence_hash": _evidence_hash(row),
    }


def _unique_rows_by_identity(rows: list[Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], set[tuple[str, str]]]:
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _board_identity(row)
        if identity is None:
            continue
        if identity in by_identity:
            ambiguous.add(identity)
        else:
            by_identity[identity] = row
    for identity in ambiguous:
        by_identity.pop(identity, None)
    return by_identity, ambiguous


def _identity_counts(pairs: Any) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for pair in pairs:
        identity = _pair_identity(pair)
        if identity is not None:
            counts[identity] = counts.get(identity, 0) + 1
    return counts


def _pair_identity(pair: dict[str, Any]) -> tuple[str, str] | None:
    polymarket = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
    kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
    poly_id = _string_or_empty(polymarket.get("market_id") or polymarket.get("condition_id"))
    kalshi_id = _string_or_empty(kalshi.get("ticker") or kalshi.get("market_ticker") or kalshi.get("market_id"))
    if not poly_id or not kalshi_id:
        return None
    return (poly_id, kalshi_id)


def _board_identity(row: dict[str, Any]) -> tuple[str, str] | None:
    polymarket = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
    kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
    poly_id = _string_or_empty(polymarket.get("market_id") or polymarket.get("condition_id"))
    kalshi_id = _string_or_empty(kalshi.get("ticker") or kalshi.get("market_ticker") or kalshi.get("market_id"))
    if not poly_id or not kalshi_id:
        return None
    return (poly_id, kalshi_id)


def _board_row_id(row: dict[str, Any]) -> str:
    identity = _board_identity(row)
    if identity is None:
        return "unmatched"
    return f"{identity[0]}__{identity[1]}"


def _evidence_hash(row: dict[str, Any]) -> str:
    evidence_payload = {
        "identity": _board_identity(row),
        "same_payoff": row.get("same_payoff"),
        "strict_pass_count": row.get("strict_pass_count"),
        "strict_comparator_count": row.get("strict_comparator_count"),
        "blockers": row.get("blockers") or [],
        "missing_fields": row.get("missing_fields") or [],
        "strict_blockers": row.get("strict_blockers") or [],
        "strict_missing_fields": row.get("strict_missing_fields") or [],
        "info_blockers": row.get("info_blockers") or [],
        "info_missing_fields": row.get("info_missing_fields") or [],
        "same_payoff_evidence": row.get("same_payoff_evidence") or {},
    }
    encoded = json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_blockers_and_missing(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    if "strict_blockers" in row or "strict_missing_fields" in row:
        return _string_list(row.get("strict_blockers")), _string_list(row.get("strict_missing_fields"))

    blockers: list[str] = []
    missing_fields: list[str] = []
    evidence = row.get("same_payoff_evidence")
    if not isinstance(evidence, dict):
        return blockers, missing_fields
    for comparator in evidence.values():
        if not isinstance(comparator, dict):
            continue
        if comparator.get("strict") is False:
            continue
        blockers.extend(_string_list(comparator.get("blockers")))
        missing_fields.extend(_string_list(comparator.get("missing_fields")))
    return sorted(set(blockers)), sorted(set(missing_fields))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def _validate_schema_one(label: str, payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
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


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
