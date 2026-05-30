from __future__ import annotations

import json
from pathlib import Path

import scan
from relative_value.batch_evidence_import_readiness import build_batch_evidence_import_readiness_report


def test_batch_readiness_dedupes_and_prefers_polished_crypto(tmp_path: Path) -> None:
    raw = tmp_path / "automation_batch_001"
    polished = tmp_path / "automation_batch_001_polished"
    raw.mkdir()
    polished.mkdir()
    _write_index(
        raw,
        [
            {
                "family": "btc_price_threshold",
                "category": "crypto",
                "readiness": "needs_rule_review",
                "kalshi": True,
                "polymarket": True,
                "blockers": ["raw_blocker"],
            }
        ],
    )
    _write_index(
        polished,
        [
            {
                "family": "btc_price_threshold",
                "category": "crypto",
                "readiness": "ready_for_crypto_basis_review",
                "kalshi": True,
                "polymarket": True,
                "blockers": ["basis_risk_review_required"],
            }
        ],
    )

    report = build_batch_evidence_import_readiness_report(input_roots=[raw, polished])
    row = next(row for row in report["families"] if row["family"] == "btc_price_threshold")

    assert row["readiness_class"] == "READY_FOR_CRYPTO_BASIS_SCOUT"
    assert "polished" in row["source_root"]
    assert report["crypto_worklist"][0]["family"] == "btc_price_threshold"
    assert report["exact_ready_rows"] == 0
    assert report["paper_candidate_rows"] == 0


def test_batch_readiness_routes_cdna_and_graph_worklists(tmp_path: Path) -> None:
    root = tmp_path / "automation_batch_002"
    root.mkdir()
    _write_index(
        root,
        [
            {
                "family": "cdna_fill_first/nba_champion_2026",
                "category": "sports",
                "readiness": "ready_for_cdna_fill_first_scout",
                "cdna": True,
                "blockers": ["cdna_executable_size_unverified"],
            },
            {
                "family": "nfl_division_winners",
                "category": "sports",
                "readiness": "ready_for_graph_review",
                "kalshi": True,
                "polymarket": True,
                "blockers": ["missing_graph_relationship"],
            },
        ],
    )

    report = build_batch_evidence_import_readiness_report(input_roots=[root])

    assert report["cdna_fill_first_worklist"][0]["readiness_class"] == "READY_FOR_CDNA_FILL_FIRST_SCOUT"
    assert report["graph_worklist"][0]["readiness_class"] == "READY_FOR_GRAPH_REVIEW"


def test_scan_command_writes_batch_readiness_report(tmp_path: Path) -> None:
    root = tmp_path / "automation_batch_001_polished"
    root.mkdir()
    _write_index(root, [{"family": "eth_price_threshold", "category": "crypto", "readiness": "ready_for_crypto_basis_review", "kalshi": True, "polymarket": True}])
    json_output = tmp_path / "readiness.json"
    md_output = tmp_path / "readiness.md"

    rc = scan.main(
        [
            "batch-evidence-import-readiness",
            "--input-roots",
            str(root),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "batch_evidence_import_readiness_v1"
    assert payload["crypto_worklist"][0]["family"] == "eth_price_threshold"


def _write_index(root: Path, rows: list[dict]) -> None:
    (root / "_index.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
