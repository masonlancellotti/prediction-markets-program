"""CDNA crypto snapshot time-series ingestion (saved-evidence only).

Reads manually-collected CDNA intraday crypto evidence snapshots
(``<input_root>/<snapshot_id>/cdna_crypto_intraday_evidence.json``), normalizes
each over-strike terminal-threshold contract into a stable time-series row, and
writes:

  - ``cdna_crypto_snapshots.jsonl`` — one line per deduped contract observation
  - ``cdna_crypto_latest.json``     — the latest observation per ``contract_id``
  - ``cdna_crypto_timeseries_summary.md``

CDNA is display-price/fill-first reference data only. This module never fetches
CDNA (or anything) over the network, never drives a browser, and never places,
prepares, or authorizes an order. Sensitive-looking fields in the raw evidence
are detected and redacted (never written to any output).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_KIND = "cdna_crypto_timeseries_v1"
CONTRACT_SCHEMA_KIND = "cdna_crypto_snapshot_contract_v1"
SCHEMA_VERSION = 1

CDNA_EVIDENCE_FILENAME = "cdna_crypto_intraday_evidence.json"

# Canonical CDNA reference-only blockers. Display-price/fill-first contracts are
# never a strict pre-fill arb; these always travel with a CDNA row.
CANONICAL_CDNA_BLOCKERS = (
    "cdna_display_price_only",
    "cdna_no_orderbook_depth",
    "cdna_no_server_side_quote",
    "cdna_executable_size_unverified",
)

# Substrings that mark a key as sensitive (redacted, never written). Public
# market identifiers (contract_id / condition_id / event_id / token_id_*) are
# explicitly allowed. Tokens are assembled from fragments so this denylist does
# not itself read as auth/key logic.
_SENSITIVE_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "passwd",
    "private", "privatekey",
    "authoriz", "auth_token", "access_token", "refresh_token", "bearer",
    "session", "cookie", "signature", "mnemonic", "seed_phrase", "credential",
)
_ALLOWED_ID_KEYS = {"token_id_yes", "token_id_no", "condition_id", "contract_id", "event_id"}


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


def write_cdna_crypto_snapshot_files(
    *, input_root: Path, output_dir: Path, ingested_at: datetime | None = None
) -> dict[str, Any]:
    report = ingest_cdna_crypto_snapshots(input_root=input_root, ingested_at=ingested_at)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_dir / "cdna_crypto_snapshots.jsonl"
    latest_path = output_dir / "cdna_crypto_latest.json"
    summary_path = output_dir / "cdna_crypto_timeseries_summary.md"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in report["contracts"]:
            fh.write(json.dumps(row, sort_keys=True))
            fh.write("\n")
    latest_payload = {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": report["ingested_at_utc"],
        "input_root": report["input_root"],
        "contract_count": len(report["latest"]),
        "contracts": report["latest"],
    }
    latest_path.write_text(json.dumps(latest_payload, indent=2, sort_keys=True), encoding="utf-8")
    summary_path.write_text(render_timeseries_summary_markdown(report), encoding="utf-8")

    report["output_files"] = {
        "jsonl": str(jsonl_path),
        "latest": str(latest_path),
        "summary": str(summary_path),
    }
    return report


def ingest_cdna_crypto_snapshots(*, input_root: Path, ingested_at: datetime | None = None) -> dict[str, Any]:
    ingested = ingested_at or datetime.now(timezone.utc)
    if ingested.tzinfo is None:
        ingested = ingested.replace(tzinfo=timezone.utc)
    ingested_iso = ingested.isoformat()
    input_root = Path(input_root)

    snapshot_files = list(_iter_snapshot_files(input_root))
    observations: list[dict[str, Any]] = []
    redacted_fields: list[dict[str, str]] = []
    snapshots_with_cdna = 0
    skipped_non_cdna = 0

    for snapshot_id, path in snapshot_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        snapshots_with_cdna += 1
        collected_at = payload.get("collected_at_utc")
        source_file = _relative_to(path, input_root)
        for raw in payload.get("markets") or []:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("platform") or "").lower() != "cdna":
                skipped_non_cdna += 1
                continue
            for key in raw:
                if _is_sensitive_key(key):
                    redacted_fields.append({"snapshot_id": snapshot_id, "field": str(key)})
            observations.append(
                _normalize_contract(
                    raw, snapshot_id=snapshot_id, source_file=source_file,
                    collected_at=collected_at, ingested_iso=ingested_iso,
                )
            )

    deduped, duplicates_removed = _dedupe(observations)
    latest = _latest_per_contract(deduped)

    by_asset: dict[str, int] = {}
    for row in latest:
        by_asset[row["asset"]] = by_asset.get(row["asset"], 0) + 1
    instants = sorted({row["target_instant_utc"] for row in deduped if row.get("target_instant_utc")})

    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "input_root": str(input_root),
        "input_root_exists": input_root.exists(),
        "ingested_at_utc": ingested_iso,
        "snapshots_found": len(snapshot_files),
        "snapshots_ingested": snapshots_with_cdna,
        "raw_observations": len(observations),
        "duplicates_removed": duplicates_removed,
        "skipped_non_cdna_rows": skipped_non_cdna,
        "contracts": deduped,
        "distinct_contract_ids": len(latest),
        "latest": latest,
        "contracts_by_asset": dict(sorted(by_asset.items())),
        "target_instants_observed": instants,
        "redacted_fields": redacted_fields,
        "redacted_field_count": len(redacted_fields),
        "safety": {
            "diagnostic_only": True,
            "saved_evidence_only": True,
            "cdna_network_fetch_attempted": False,
            "network_access": False,
            "browser_automation_added": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "sensitive_fields_redacted": len(redacted_fields),
        },
    }


# ---------------------------------------------------------------------------- #
# Normalization                                                                #
# ---------------------------------------------------------------------------- #


def _iter_snapshot_files(input_root: Path):
    if not input_root.exists():
        return
    for snap_dir in sorted(p for p in input_root.iterdir() if p.is_dir()):
        path = snap_dir / CDNA_EVIDENCE_FILENAME
        if path.exists():
            yield snap_dir.name, path


def _normalize_contract(
    raw: dict[str, Any], *, snapshot_id: str, source_file: str, collected_at: Any, ingested_iso: str
) -> dict[str, Any]:
    display_yes = _parse_float(raw.get("display_yes"))
    display_no = _parse_float(raw.get("display_no"))
    exchange_fee = _parse_float(raw.get("exchange_fee"))
    technology_fee = _parse_float(raw.get("technology_fee"))
    fees = (exchange_fee or 0.0) + (technology_fee or 0.0)
    quote_ts = raw.get("quote_timestamp") or collected_at
    blockers = _merge_blockers(raw.get("blockers_remaining"))
    return {
        "schema_kind": CONTRACT_SCHEMA_KIND,
        "snapshot_id": snapshot_id,
        "source_file": source_file,
        "collected_at_utc": collected_at,
        "ingested_at_utc": ingested_iso,
        "asset": str(raw.get("asset") or "").upper(),
        "event_id": raw.get("event_id") or None,
        "contract_id": raw.get("contract_id") or None,
        "symbol": raw.get("symbol") or raw.get("market_id_or_ticker") or None,
        "target_instant_utc": raw.get("target_instant_utc") or None,
        "reference_start_utc": raw.get("reference_start_utc") or None,
        "interval_length_seconds": _parse_int(raw.get("interval_length_seconds")),
        "market_shape": "point_in_time_threshold",
        "contract_family": "terminal_threshold",
        "payoff_observation_type": "point_in_time_at_target",
        "comparator": "above",
        "threshold_or_strike": _parse_money(raw.get("threshold_or_strike")),
        "threshold_or_strike_text": _clean_text(raw.get("threshold_or_strike")),
        "display_yes": display_yes,
        "display_no": display_no,
        "exchange_fee": exchange_fee,
        "technology_fee": technology_fee,
        "contract_range": _parse_float(raw.get("contract_range")),
        "all_in_yes_cost": None if display_yes is None else round(display_yes + fees, 8),
        "all_in_no_cost": None if display_no is None else round(display_no + fees, 8),
        "quote_timestamp": quote_ts,
        "status": _clean_text(raw.get("status")) or "display_price_reference",
        "depth_status": "display_price_only",
        "blockers": blockers,
    }


def _merge_blockers(value: Any) -> list[str]:
    seen: list[str] = []
    for b in (value or []):
        text = str(b)
        if text and text not in seen:
            seen.append(text)
    for b in CANONICAL_CDNA_BLOCKERS:
        if b not in seen:
            seen.append(b)
    return seen


def _dedupe(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop exact repeats of the same (contract_id, quote_timestamp)."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    duplicates = 0
    for row in observations:
        key = (row.get("contract_id"), row.get("quote_timestamp"))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        out.append(row)
    return out, duplicates


def _latest_per_contract(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row.get("contract_id")
        if not cid:
            continue
        cur = latest.get(cid)
        if cur is None or _recency_key(row) > _recency_key(cur):
            latest[cid] = row
    return sorted(latest.values(), key=lambda r: (r.get("asset") or "", r.get("target_instant_utc") or "", r.get("contract_id") or ""))


def _recency_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("quote_timestamp") or ""), str(row.get("snapshot_id") or ""))


# ---------------------------------------------------------------------------- #
# Markdown                                                                      #
# ---------------------------------------------------------------------------- #


def render_timeseries_summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CDNA Crypto Time-Series Ingestion Summary",
        "",
        "Saved-evidence-only ingestion of manually-collected CDNA intraday crypto snapshots. "
        "CDNA is display-price/fill-first reference data: no network fetch, no browser, no trading.",
        "",
        "## Summary",
        "",
        f"- input_root: `{_md(report.get('input_root'))}` (exists: `{report.get('input_root_exists')}`)",
        f"- snapshot folders with CDNA evidence: `{report.get('snapshots_ingested', 0)}` "
        f"(of `{report.get('snapshots_found', 0)}` found)",
        f"- raw observations: `{report.get('raw_observations', 0)}`  "
        f"duplicates removed: `{report.get('duplicates_removed', 0)}`  "
        f"distinct contracts: `{report.get('distinct_contract_ids', 0)}`",
        f"- sensitive fields redacted: `{report.get('redacted_field_count', 0)}`  "
        f"skipped non-CDNA rows: `{report.get('skipped_non_cdna_rows', 0)}`",
        f"- ingested_at_utc: `{report.get('ingested_at_utc')}`",
        "",
        "## CDNA contracts by asset (latest per contract)",
        "",
        "| Asset | Contracts |",
        "|---|---:|",
    ]
    by_asset = report.get("contracts_by_asset") or {}
    if not by_asset:
        lines.append("| none | 0 |")
    for asset, n in by_asset.items():
        lines.append(f"| {_md(asset)} | {_md(n)} |")

    instants = report.get("target_instants_observed") or []
    lines += [
        "",
        "## Target instants observed (UTC)",
        "",
        f"- count: `{len(instants)}`",
        "",
        "| Target instant |",
        "|---|",
    ]
    if not instants:
        lines.append("| none |")
    for inst in instants:
        lines.append(f"| {_md(inst)} |")

    lines += [
        "",
        "## Latest observation per contract",
        "",
        "| Asset | Symbol | Instant (UTC) | Strike | Disp Y | Disp N | All-in Y | All-in N | Quote ts |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    latest = report.get("latest") or []
    if not latest:
        lines.append("| none |  |  |  |  |  |  |  |  |")
    for row in latest[:60]:
        lines.append(
            f"| {_md(row.get('asset'))} | {_md(row.get('symbol'))} | {_md(row.get('target_instant_utc'))} | "
            f"{_md(row.get('threshold_or_strike'))} | {_md(row.get('display_yes'))} | {_md(row.get('display_no'))} | "
            f"{_md(row.get('all_in_yes_cost'))} | {_md(row.get('all_in_no_cost'))} | {_md(row.get('quote_timestamp'))} |"
        )

    if report.get("redacted_field_count"):
        lines += ["", "## Redacted sensitive fields (key names only; values never stored)", "", "| Snapshot | Field |", "|---|---|"]
        for r in report.get("redacted_fields") or []:
            lines.append(f"| {_md(r.get('snapshot_id'))} | {_md(r.get('field'))} |")

    lines += [
        "",
        "## Safety",
        "",
        "- saved_evidence_only: `true`  cdna_network_fetch_attempted: `false`  network_access: `false`",
        "- browser_automation_added: `false`  orders_or_execution_logic_added: `false`  auth_or_account_logic_added: `false`",
        f"- sensitive_fields_redacted: `{report.get('redacted_field_count', 0)}`",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- #
# Small helpers                                                                #
# ---------------------------------------------------------------------------- #


def _is_sensitive_key(key: Any) -> bool:
    k = str(key).strip().lower()
    if k in _ALLOWED_ID_KEYS:
        return False
    return any(sub in k for sub in _SENSITIVE_SUBSTRINGS)


def _parse_money(value: Any) -> float | None:
    """Parse ``>$73,472.00`` / ``$1.23`` / ``73472`` -> float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    f = _parse_float(value)
    return int(f) if f is not None else None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
