from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.normalize import parse_datetime
from relative_value.source_registry import SOURCE_REGISTRY, SourceType


LIVE_API = "LIVE_API"
SAVED_SNAPSHOT = "SAVED_SNAPSHOT"
STATIC_FIXTURE = "STATIC_FIXTURE"
MOCK_SAMPLE = "MOCK_SAMPLE"
UNKNOWN = "UNKNOWN"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

THE_ODDS_API_KEY_ENV = "THE_ODDS_API_KEY"


def build_fixture_scan_provenance(fixture_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    timestamp = now or datetime.now(timezone.utc)
    sources = [
        _fixture_source(
            source_id="kalshi",
            snapshot_path=fixture_dir / "kalshi_markets.json",
            requires_api_key=False,
            api_key_env_var=None,
            timestamp=timestamp,
        ),
        _fixture_source(
            source_id="polymarket",
            snapshot_path=fixture_dir / "polymarket_markets.json",
            requires_api_key=False,
            api_key_env_var=None,
            timestamp=timestamp,
        ),
        _fixture_source(
            source_id="the_odds_api",
            snapshot_path=fixture_dir / "the_odds_api_events.json",
            requires_api_key=True,
            api_key_env_var=THE_ODDS_API_KEY_ENV,
            timestamp=timestamp,
        ),
    ]
    return {
        "data_source_mode": STATIC_FIXTURE,
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
        "description": "Default python scan.py uses local fixture/sample files only; no live API calls are attempted.",
        "sources": sources,
    }


def source_readiness_report(*, env: dict[str, str] | None = None) -> dict[str, Any]:
    environment = env if env is not None else os.environ
    rows = [
        _readiness_row(
            source_id="kalshi",
            display_name="Kalshi",
            account_needed="no",
            api_key_needed="no",
            api_key_env_var=None,
            live_fetch_implemented=True,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=STATIC_FIXTURE,
            source_type=_registry_source_type("kalshi"),
            can_participate_in_candidate_pair=True,
            can_create_paper_candidate=False,
            next_required_connection_step=(
                "Use fetch-kalshi for public read-only discovery; candidate eligibility still requires "
                "paired executable venue data, fresh depth, fees, and same-payoff review."
            ),
            env=environment,
        ),
        _readiness_row(
            source_id="polymarket",
            display_name="Polymarket",
            account_needed="no",
            api_key_needed="no",
            api_key_env_var=None,
            live_fetch_implemented=True,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=STATIC_FIXTURE,
            source_type=_registry_source_type("polymarket"),
            can_participate_in_candidate_pair=True,
            can_create_paper_candidate=False,
            next_required_connection_step=(
                "Use fetch-polymarket for public read-only Gamma discovery; candidate eligibility still "
                "requires paired executable venue data, fresh depth, fees, and same-payoff review."
            ),
            env=environment,
        ),
        _readiness_row(
            source_id="the_odds_api",
            display_name="The Odds API",
            account_needed="yes",
            api_key_needed="yes",
            api_key_env_var=THE_ODDS_API_KEY_ENV,
            live_fetch_implemented=True,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=STATIC_FIXTURE,
            source_type=_registry_source_type("the_odds_api"),
            can_participate_in_candidate_pair=False,
            can_create_paper_candidate=False,
            next_required_connection_step=(
                f"Configure {THE_ODDS_API_KEY_ENV} or pass --api-key for explicit fetch-the-odds-api runs; "
                "outputs remain REFERENCE_ONLY diagnostics."
            ),
            env=environment,
        ),
        _readiness_row(
            source_id="sx_bet",
            display_name="SX Bet",
            account_needed="no",
            api_key_needed="no",
            api_key_env_var=None,
            live_fetch_implemented=False,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=NOT_IMPLEMENTED,
            source_type=_registry_source_type("sx_bet"),
            can_participate_in_candidate_pair=False,
            can_create_paper_candidate=False,
            next_required_connection_step="Build a separately reviewed live-read-only raw fetcher; keep wallet/signing/execution out of scope.",
            env=environment,
        ),
        _readiness_row(
            source_id="prophetx",
            display_name="ProphetX",
            account_needed="yes",
            api_key_needed="unknown",
            api_key_env_var=None,
            live_fetch_implemented=False,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=NOT_IMPLEMENTED,
            source_type=_registry_source_type("prophetx"),
            can_participate_in_candidate_pair=False,
            can_create_paper_candidate=False,
            next_required_connection_step="Fixture schema exists; confirm API access/eligibility and review settlement, fee, depth, and freshness before live transport.",
            env=environment,
        ),
        _readiness_row(
            source_id="forecastex_ibkr",
            display_name="IBKR / ForecastEx",
            account_needed="yes",
            api_key_needed="unknown",
            api_key_env_var=None,
            live_fetch_implemented=True,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=NOT_IMPLEMENTED,
            source_type=_registry_source_type("forecastex_ibkr"),
            can_participate_in_candidate_pair=False,
            can_create_paper_candidate=False,
            next_required_connection_step=(
                "Live read-only diagnostic fetch exists after manual Gateway login; keep source registry planned, "
                "candidate-pair participation disabled, and paper-candidate creation disabled until settlement, fee, "
                "quote, and exact-payoff reviews are complete."
            ),
            env=environment,
        ),
        _readiness_row(
            source_id="crypto_com",
            display_name="Crypto.com",
            account_needed="yes",
            api_key_needed="unknown",
            api_key_env_var=None,
            live_fetch_implemented=False,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=NOT_IMPLEMENTED,
            source_type=SourceType.DO_NOT_USE_YET.value,
            can_participate_in_candidate_pair=False,
            can_create_paper_candidate=False,
            next_required_connection_step="Do source taxonomy and product/settlement fit review before adding any read-only adapter.",
            env=environment,
        ),
        _readiness_row(
            source_id="robinhood",
            display_name="Robinhood",
            account_needed="yes",
            api_key_needed="unknown",
            api_key_env_var=None,
            live_fetch_implemented=False,
            live_fetch_currently_used_by_scan_py=False,
            source_mode_currently_used=NOT_IMPLEMENTED,
            source_type=SourceType.DO_NOT_USE_YET.value,
            can_participate_in_candidate_pair=False,
            can_create_paper_candidate=False,
            next_required_connection_step="Do API-permission, eligibility, and read-only data review before any implementation.",
            env=environment,
        ),
    ]
    return {
        "schema_version": 1,
        "source": "source_readiness",
        "default_scan_data_source_mode": STATIC_FIXTURE,
        "default_scan_live_fetch_attempted": False,
        "rows": rows,
    }


def _fixture_source(
    *,
    source_id: str,
    snapshot_path: Path,
    requires_api_key: bool,
    api_key_env_var: str | None,
    timestamp: datetime,
) -> dict[str, Any]:
    captured_at = _latest_captured_at(snapshot_path)
    return {
        "data_source_mode": STATIC_FIXTURE,
        "source_id": source_id,
        "source_type": _registry_source_type(source_id),
        "snapshot_path": str(snapshot_path),
        "captured_at": captured_at.isoformat() if captured_at else None,
        "quote_age_seconds": _quote_age_seconds(captured_at, timestamp),
        "requires_api_key": requires_api_key,
        "api_key_env_var": api_key_env_var,
        "api_key_configured": _api_key_configured(api_key_env_var),
        "live_fetch_attempted": False,
        "live_fetch_succeeded": False,
    }


def _latest_captured_at(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    values: list[datetime] = []
    if isinstance(payload, dict):
        candidates: list[Any] = [payload.get("captured_at"), payload.get("retrieved_at")]
        for key in ("normalized_markets", "records", "markets"):
            rows = payload.get(key)
            if isinstance(rows, list):
                candidates.extend(_row_timestamps(rows))
    elif isinstance(payload, list):
        candidates = _row_timestamps(payload)
    else:
        candidates = []
    for candidate in candidates:
        parsed = parse_datetime(candidate)
        if parsed is not None:
            values.append(parsed)
    if not values:
        return None
    return max(values)


def _row_timestamps(rows: list[Any]) -> list[Any]:
    timestamps: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            timestamps.extend([row.get("captured_at"), row.get("retrieved_at"), row.get("quote_captured_at")])
    return timestamps


def _quote_age_seconds(captured_at: datetime | None, timestamp: datetime) -> float | None:
    if captured_at is None:
        return None
    return max(0.0, round((timestamp - captured_at).total_seconds(), 6))


def _readiness_row(
    *,
    source_id: str,
    display_name: str,
    account_needed: str,
    api_key_needed: str,
    api_key_env_var: str | None,
    live_fetch_implemented: bool,
    live_fetch_currently_used_by_scan_py: bool,
    source_mode_currently_used: str,
    source_type: str,
    can_participate_in_candidate_pair: bool,
    can_create_paper_candidate: bool,
    next_required_connection_step: str,
    env: dict[str, str],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "display_name": display_name,
        "account_needed": account_needed,
        "api_key_needed": api_key_needed,
        "api_key_env_var": api_key_env_var,
        "api_key_configured": bool(api_key_env_var and env.get(api_key_env_var)),
        "live_fetch_implemented": live_fetch_implemented,
        "live_fetch_currently_used_by_scan_py": live_fetch_currently_used_by_scan_py,
        "source_mode_currently_used": source_mode_currently_used,
        "source_type": source_type,
        "can_participate_in_candidate_pair": can_participate_in_candidate_pair,
        "can_create_paper_candidate": can_create_paper_candidate,
        "next_required_connection_step": next_required_connection_step,
    }


def _api_key_configured(api_key_env_var: str | None) -> bool:
    return bool(api_key_env_var and os.environ.get(api_key_env_var))


def _registry_source_type(source_id: str) -> str:
    entry = SOURCE_REGISTRY.get(source_id)
    if entry is None:
        return UNKNOWN
    return entry.source_type.value
