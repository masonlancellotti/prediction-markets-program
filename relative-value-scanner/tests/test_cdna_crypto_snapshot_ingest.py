"""Tests for the CDNA crypto snapshot time-series ingestion (no network)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import scan
from relative_value.cdna_crypto_snapshot_ingest import (
    ingest_cdna_crypto_snapshots,
    render_timeseries_summary_markdown,
    write_cdna_crypto_snapshot_files,
)


def _contract(asset="BTC", cid="c-btc-1", strike=">$73,472.00", dy="0.45", dn="0.66",
              quote_ts="2026-05-30T09:25:33Z", instant="2026-05-30T09:40:00Z", **extra):
    c = {
        "asset": asset, "platform": "cdna", "contract_family": "terminal_threshold",
        "market_shape": "point_in_time_threshold", "payoff_observation_type": "point_in_time_at_target",
        "comparator": "above", "reference_start_utc": "2026-05-30T09:20:00Z",
        "target_instant_utc": instant, "interval_length_seconds": 1200,
        "threshold_or_strike": strike, "display_yes": dy, "display_no": dn,
        "exchange_fee": "0.02", "technology_fee": "0.00", "contract_range": "1.00",
        "event_id": "e-1", "contract_id": cid, "symbol": f"{asset}USD_X.NXO",
        "quote_timestamp": quote_ts, "depth_status": "display_price_only",
        "blockers_remaining": ["cdna_display_price_only", "cdna_reference_only"],
    }
    c.update(extra)
    return c


def _write_snapshot(root: Path, ts: str, markets: list[dict[str, Any]], collected_at=None) -> None:
    d = root / ts
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_kind": "crypto_intraday_visible_chrome_evidence_v1", "platform": "cdna",
        "collected_at_utc": collected_at or "2026-05-30T09:25:33Z",
        "markets": markets,
    }
    (d / "cdna_crypto_intraday_evidence.json").write_text(json.dumps(payload), encoding="utf-8")


def test_ingests_multiple_snapshot_folders(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    _write_snapshot(root, "20260530T092439Z", [_contract(asset="BTC", cid="b1"), _contract(asset="ETH", cid="e1")])
    _write_snapshot(root, "20260530T100001Z", [_contract(asset="BTC", cid="b1", quote_ts="2026-05-30T09:55:00Z"), _contract(asset="SOL", cid="s1")])
    rep = ingest_cdna_crypto_snapshots(input_root=root)
    assert rep["snapshots_ingested"] == 2
    assert rep["raw_observations"] == 4
    assert set(rep["contracts_by_asset"]) == {"BTC", "ETH", "SOL"}


def test_dedupes_same_contract_and_quote_timestamp(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    same = _contract(cid="b1", quote_ts="2026-05-30T09:25:33Z")
    _write_snapshot(root, "20260530T092439Z", [same])
    _write_snapshot(root, "20260530T092955Z", [dict(same)])  # identical contract_id+quote_ts
    rep = ingest_cdna_crypto_snapshots(input_root=root)
    assert rep["raw_observations"] == 2
    assert rep["duplicates_removed"] == 1
    assert len(rep["contracts"]) == 1


def test_parses_display_price_and_strike(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    _write_snapshot(root, "20260530T092439Z", [_contract(strike=">$73,472.00", dy="0.45", dn="0.66")])
    rep = ingest_cdna_crypto_snapshots(input_root=root)
    c = rep["contracts"][0]
    assert c["threshold_or_strike"] == 73472.0
    assert c["threshold_or_strike_text"] == ">$73,472.00"
    assert c["display_yes"] == 0.45 and c["display_no"] == 0.66
    assert c["all_in_yes_cost"] == 0.47  # 0.45 + 0.02 + 0.00
    assert c["all_in_no_cost"] == 0.68
    assert c["market_shape"] == "point_in_time_threshold"
    assert c["depth_status"] == "display_price_only"
    assert set(["cdna_display_price_only", "cdna_no_orderbook_depth",
                "cdna_no_server_side_quote", "cdna_executable_size_unverified"]).issubset(set(c["blockers"]))


def test_latest_keeps_latest_per_contract(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    _write_snapshot(root, "20260530T092439Z", [_contract(cid="b1", dy="0.45", quote_ts="2026-05-30T09:25:33Z")])
    _write_snapshot(root, "20260530T100001Z", [_contract(cid="b1", dy="0.51", quote_ts="2026-05-30T09:55:00Z")])
    rep = ingest_cdna_crypto_snapshots(input_root=root)
    assert len(rep["contracts"]) == 2  # time series keeps both observations
    assert rep["distinct_contract_ids"] == 1
    latest = rep["latest"][0]
    assert latest["quote_timestamp"] == "2026-05-30T09:55:00Z"
    assert latest["display_yes"] == 0.51


def test_detects_and_redacts_sensitive_fields(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    leaky = _contract(cid="b1")
    leaky["api_key"] = "SECRET-DEADBEEF-DO-NOT-LEAK"
    leaky["authorization"] = "Bearer abc123"
    _write_snapshot(root, "20260530T092439Z", [leaky])
    pack = write_cdna_crypto_snapshot_files(
        input_root=root, output_dir=tmp_path / "out",
    )
    assert pack["redacted_field_count"] >= 2
    fields = {r["field"] for r in pack["redacted_fields"]}
    assert "api_key" in fields and "authorization" in fields
    # The secret value must never appear in any output file.
    for name in ("cdna_crypto_snapshots.jsonl", "cdna_crypto_latest.json", "cdna_crypto_timeseries_summary.md"):
        text = (tmp_path / "out" / name).read_text(encoding="utf-8")
        assert "DEADBEEF" not in text and "Bearer abc123" not in text
    # public identifiers are NOT treated as sensitive.
    assert "contract_id" not in fields and "token_id_yes" not in fields


def test_writes_outputs_and_summary_sections(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    _write_snapshot(root, "20260530T092439Z", [_contract(asset="BTC", cid="b1"), _contract(asset="DOGE", cid="d1")])
    out = tmp_path / "out"
    rep = write_cdna_crypto_snapshot_files(input_root=root, output_dir=out)
    assert (out / "cdna_crypto_snapshots.jsonl").exists()
    assert (out / "cdna_crypto_latest.json").exists()
    assert (out / "cdna_crypto_timeseries_summary.md").exists()
    lines = (out / "cdna_crypto_snapshots.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2 and all(json.loads(ln)["schema_kind"] == "cdna_crypto_snapshot_contract_v1" for ln in lines)
    latest = json.loads((out / "cdna_crypto_latest.json").read_text(encoding="utf-8"))
    assert latest["schema_kind"] == "cdna_crypto_timeseries_v1" and latest["contract_count"] == 2
    md = (out / "cdna_crypto_timeseries_summary.md").read_text(encoding="utf-8")
    for header in ("## Summary", "## CDNA contracts by asset", "## Target instants observed", "## Latest observation per contract"):
        assert header in md


def test_skips_non_cdna_and_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    mixed = _contract(cid="b1")
    poly = {"asset": "BTC", "platform": "polymarket", "contract_id": "p1", "quote_timestamp": "t"}
    _write_snapshot(root, "20260530T092439Z", [mixed, poly])
    rep = ingest_cdna_crypto_snapshots(input_root=root)
    assert rep["raw_observations"] == 1
    assert rep["skipped_non_cdna_rows"] == 1
    empty = ingest_cdna_crypto_snapshots(input_root=tmp_path / "nope")
    assert empty["input_root_exists"] is False and empty["snapshots_ingested"] == 0


def test_scan_cli_runs_ingest(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    _write_snapshot(root, "20260530T092439Z", [_contract(cid="b1")])
    rc = scan.main([
        "ingest-cdna-crypto-snapshots",
        "--input-root", str(root),
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 0
    assert (tmp_path / "out" / "cdna_crypto_latest.json").exists()


def test_no_network_browser_trading_auth_code() -> None:
    src = Path("relative_value/cdna_crypto_snapshot_ingest.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    forbidden = [
        r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bsign_transaction\b",
        r"\bprivate_key\b", r"\bwallet\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
        r"requests\.(get|post|put|delete|patch)", r"\bhttpx\b", r"\burlopen\b", r"\burllib\b",
        r"\bAuthorization\b", r"\bsmtp\b", r"\bslack\b", r"\bwebhook\b",
    ]
    for pat in forbidden:
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden pattern {pat} in cdna ingest module"
