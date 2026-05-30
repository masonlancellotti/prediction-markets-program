from __future__ import annotations

import json
from pathlib import Path

from relative_value.cdna_fill_log import (
    append_cdna_fill_record,
    build_cdna_fill_record,
    load_cdna_fill_log,
    record_cdna_fill_file,
)


def test_cdna_fill_log_appends_manual_record(tmp_path: Path) -> None:
    fill_log = tmp_path / "fills.json"

    report = record_cdna_fill_file(
        fill_log=fill_log,
        event_key="NBA_CHAMPION_2026",
        market_family="sports_championship_futures",
        team="Oklahoma City Thunder",
        side="YES",
        contract_id="contract-okc",
        symbol="NBA-OKC",
        requested_quantity=2,
        filled_quantity=1,
        filled_price=0.43,
        fee_per_contract=0.02,
        filled_at="2026-05-29T15:02:11Z",
        source_note="manual operator record",
    )

    assert report["record_written"] is True
    payload = load_cdna_fill_log(fill_log)
    record = payload["records"][0]
    assert record["schema_kind"] == "cdna_manual_fill_record_v1"
    assert record["partial"] is True
    assert record["all_in_filled_cost"] == 0.45
    assert record["residual_unhedged_cdna_quantity"] == 1.0


def test_cdna_fill_log_rejects_sensitive_fields_without_writing(tmp_path: Path) -> None:
    fill_log = tmp_path / "fills.json"
    record = build_cdna_fill_record(
        event_key="NBA_CHAMPION_2026",
        market_family="sports_championship_futures",
        team="Oklahoma City Thunder",
        side="YES",
        contract_id="contract-okc",
        symbol="NBA-OKC",
        requested_quantity=1,
        filled_quantity=1,
        filled_price=0.43,
        fee_per_contract=0.02,
        filled_at="2026-05-29T15:02:11Z",
    )
    record["account_id"] = "do-not-store"

    report = append_cdna_fill_record(fill_log, record)

    assert report["record_written"] is False
    assert "forbidden_field_present:account_id" in report["validation_errors"]
    assert not fill_log.exists()


def test_cdna_fill_log_loads_list_shape(tmp_path: Path) -> None:
    fill_log = tmp_path / "fills.json"
    fill_log.write_text(json.dumps([{"schema_kind": "cdna_manual_fill_record_v1"}]), encoding="utf-8")

    payload = load_cdna_fill_log(fill_log)

    assert payload["schema_kind"] == "cdna_manual_fill_log_v1"
    assert len(payload["records"]) == 1
