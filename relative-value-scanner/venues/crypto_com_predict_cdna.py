from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CRYPTO_COM_PREDICT_CDNA_RESEARCH_SCHEMA_KIND = "crypto_com_predict_cdna_research_snapshot_v1"

CRYPTO_COM_PREDICT_CDNA_REQUIRED_BLOCKERS = (
    "boundary_only_no_live_transport",
    "auth_region_execution_mechanics_unreviewed",
    "market_data_endpoint_not_reviewed",
    "settlement_source_not_reviewed",
    "fee_model_not_reviewed",
    "quote_freshness_not_reviewed",
    "not_integrated_with_matcher_or_evaluator",
)


def load_crypto_com_predict_cdna_research_fixtures(fixture_dir: Path) -> list[dict[str, Any]]:
    if not fixture_dir.exists():
        raise FileNotFoundError(str(fixture_dir))
    if not fixture_dir.is_dir():
        raise NotADirectoryError(str(fixture_dir))

    records: list[dict[str, Any]] = []
    for path in sorted(fixture_dir.glob("*.json")):
        payload = _load_fixture_object(path)
        if payload.get("schema_kind") == CRYPTO_COM_PREDICT_CDNA_RESEARCH_SCHEMA_KIND and isinstance(
            payload.get("markets"), list
        ):
            for index, row in enumerate(payload["markets"]):
                if isinstance(row, dict):
                    records.append(_fixture_record(row, path=path, row_index=index))
        else:
            records.append(_fixture_record(payload, path=path, row_index=0))
    return records


def _fixture_record(payload: dict[str, Any], *, path: Path, row_index: int) -> dict[str, Any]:
    record = dict(payload)
    record.setdefault("schema_kind", CRYPTO_COM_PREDICT_CDNA_RESEARCH_SCHEMA_KIND)
    record.setdefault("source_id", "crypto_com_predict_cdna")
    record.setdefault("permission", "FIXTURE_RESEARCH_ONLY")
    record.setdefault("live_fetch_attempted", False)
    record.setdefault("live_fetch_succeeded", False)
    record.setdefault("is_executable", False)
    record.setdefault("execution_allowed_in_project_now", False)
    record.setdefault("can_create_candidate_pair", False)
    record.setdefault("can_create_paper_candidate", False)
    record.setdefault("diagnostic_only", True)
    record.setdefault("affects_evaluator_gates", False)
    record.setdefault("unresolved_blockers", list(CRYPTO_COM_PREDICT_CDNA_REQUIRED_BLOCKERS))
    record["raw_source_file"] = str(path)
    record["raw_row_index"] = row_index
    return record


def _load_fixture_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"fixture must contain a JSON object: {path}")
    return payload
