"""Tests for CDNA latest-snapshot support (file-only; no network/browser/orders)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from relative_value.cdna_fast_snapshot import (
    CDNA_CANDIDATE_ACTION, CDNA_PAPER_CANDIDATE_CLASS, CdnaFastQuoteSource,
    build_cdna_fill_first_candidates, evaluate_cdna_row_freshness, load_latest_cdna_snapshot,
    partition_cdna_rows, payoff_grammar_match,
)

NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
TARGET = (NOW + timedelta(hours=1)).isoformat()
FRESH_QTS = (NOW - timedelta(seconds=10)).isoformat()
STALE_QTS = (NOW - timedelta(seconds=3600)).isoformat()


def _row(**over: Any) -> dict[str, Any]:
    row = {"contract_id": "CID1", "symbol": "CDNA-BTC-1", "asset": "BTC", "target_instant_utc": TARGET,
           "reference_start_utc": NOW.isoformat(), "interval_length_seconds": 1200,
           "contract_family": "terminal_threshold", "payoff_observation_type": "point_in_time_at_target",
           "comparator": "above", "threshold_or_strike": 73000.0, "display_yes": 0.30, "display_no": 0.68,
           "exchange_fee": 0.01, "technology_fee": 0.01, "quote_timestamp": FRESH_QTS}
    row.update(over)
    return row


def _write_latest(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_kind": "cdna_crypto_latest", "generated_at": NOW.isoformat(),
                                "contract_count": len(rows), "contracts": rows}), encoding="utf-8")
    return path


def _partner(**over: Any) -> dict[str, Any]:
    p = {"platform": "kalshi", "asset": "BTC", "target_instant_utc": TARGET, "threshold_or_strike": 73000.0,
         "comparator": "above", "contract_family": "terminal_threshold", "market_shape": "point_in_time_threshold",
         "interval_length_seconds": 3600, "ask": 0.50, "fee": 0.01, "market_id_or_ticker": "K-BTC-73000",
         "source_index": "brti", "available_size_or_cap": 50.0}
    p.update(over)
    return p


# --- 1. loads cdna_crypto_latest.json ---------------------------------------- #
def test_loads_cdna_crypto_latest_json(tmp_path: Path) -> None:
    _write_latest(tmp_path / "cdna_crypto_latest.json", [_row(), _row(contract_id="CID2")])
    snap = load_latest_cdna_snapshot(timeseries_dir=tmp_path, now=NOW)
    assert snap["loaded"] is True and snap["cdna_supplied"] is True and snap["rows_loaded"] == 2
    assert snap["missing_reason"] is None
    # missing dir -> clear reason, never raises.
    miss = load_latest_cdna_snapshot(timeseries_dir=tmp_path / "nope", now=NOW)
    assert miss["loaded"] is False and miss["missing_reason"] == "cdna_latest_file_not_found"
    assert load_latest_cdna_snapshot(now=NOW)["missing_reason"] == "cdna_timeseries_dir_not_provided"


# --- 2/3. fresh participates; stale excluded --------------------------------- #
def test_fresh_row_participates_stale_row_excluded() -> None:
    fresh = evaluate_cdna_row_freshness(_row(quote_timestamp=FRESH_QTS), now=NOW, max_age_seconds=60)
    assert fresh["fresh"] is True and not fresh["blockers"]
    stale = evaluate_cdna_row_freshness(_row(quote_timestamp=STALE_QTS), now=NOW, max_age_seconds=60)
    assert stale["fresh"] is False and "cdna_snapshot_stale" in stale["blockers"]
    expired = evaluate_cdna_row_freshness(_row(target_instant_utc=(NOW - timedelta(minutes=5)).isoformat()),
                                          now=NOW, max_age_seconds=60)
    assert "cdna_target_expired" in expired["blockers"]

    part = partition_cdna_rows([_row(), _row(contract_id="C2", quote_timestamp=STALE_QTS)], now=NOW, max_age_seconds=60)
    assert len(part["fresh_rows"]) == 1 and len(part["stale_rows"]) == 1

    gen_fresh = build_cdna_fill_first_candidates(cdna_rows=[_row()], partner_legs=[_partner()], now=NOW, require_fresh=True)
    assert gen_fresh["cdna_fill_first_candidates"] >= 1  # fresh row produced candidates
    gen_stale = build_cdna_fill_first_candidates(cdna_rows=[_row(quote_timestamp=STALE_QTS)], partner_legs=[_partner()],
                                                 now=NOW, require_fresh=True)
    assert gen_stale["cdna_fill_first_candidates"] == 0 and gen_stale["cdna_stale_rows"] == 1  # stale excluded


# --- 4/5. class + action ----------------------------------------------------- #
def test_cdna_candidate_class_and_action() -> None:
    gen = build_cdna_fill_first_candidates(cdna_rows=[_row()], partner_legs=[_partner()], now=NOW)
    assert gen["candidates"], "expected CDNA candidates"
    c = gen["candidates"][0]
    assert c["paper_candidate_class"] == CDNA_PAPER_CANDIDATE_CLASS == "CDNA_FILL_FIRST"
    assert c["candidate_action"] == CDNA_CANDIDATE_ACTION == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert c["min_payoff"] == 1.0 and c["requires_short_or_sell"] is False
    # net = 1 - (cdna_yes 0.30+0.02) - (kalshi_no 0.50+0.01) = 0.17
    yes_no = [x for x in gen["candidates"] if x["cdna_pattern"] == "cdnaYES_partnerNO"][0]
    assert abs(yes_no["net_edge_after_fees"] - 0.17) < 1e-6
    sides = [(l["platform"], l["side"]) for l in yes_no["legs"]]
    assert ("cdna", "YES") in sides and ("kalshi", "NO") in sides


# --- addendum: harmonic terminal-threshold match across interval lengths ----- #
def test_terminal_threshold_matches_across_interval_lengths() -> None:
    # CDNA 20m (1200) vs Kalshi 1h (3600), SAME target_instant/strike -> match (interval ignored).
    m = payoff_grammar_match(_row(interval_length_seconds=1200), _partner(interval_length_seconds=3600))
    assert m["match"] is True and m["interval_length_ignored_for_terminal_threshold"] is True
    # different target_instant -> blocked.
    m2 = payoff_grammar_match(_row(), _partner(target_instant_utc=(NOW + timedelta(hours=2)).isoformat()))
    assert m2["match"] is False and "target_time_mismatch" in m2["blockers"]
    # different strike -> blocked.
    m3 = payoff_grammar_match(_row(), _partner(threshold_or_strike=99999.0))
    assert "threshold_grid_mismatch" in m3["blockers"]


def test_directional_requires_same_reference_start_and_interval() -> None:
    cdna = _row(contract_family="directional_return", interval_length_seconds=1200)
    # same reference_start + interval -> match
    ok = payoff_grammar_match(cdna, _partner(contract_family="directional_return",
                                             reference_start_utc=NOW.isoformat(), interval_length_seconds=1200))
    assert ok["match"] is True and ok["interval_length_ignored_for_terminal_threshold"] is False
    # same reference_start, DIFFERENT interval -> blocked (interval matters for directional).
    bad = payoff_grammar_match(cdna, _partner(contract_family="directional_return",
                                              reference_start_utc=NOW.isoformat(), interval_length_seconds=3600))
    assert bad["match"] is False and "incompatible_contract_family" in bad["blockers"]


def test_diagnostics_top_of_hour_and_grids() -> None:
    toh = _row(target_instant_utc="2026-05-30T13:00:00Z", interval_length_seconds=1200)  # 20m landing on hour
    off = _row(contract_id="C2", target_instant_utc="2026-05-30T13:20:00Z", interval_length_seconds=1200)
    twoh = _row(contract_id="C3", target_instant_utc="2026-05-30T14:00:00Z", interval_length_seconds=7200)
    part = partition_cdna_rows([toh, off, twoh], now=NOW, max_age_seconds=10_000_000)
    assert part["cdna_top_of_hour_rows"] == 2 and part["cdna_20m_top_of_hour_rows"] == 1 and part["cdna_2h_rows"] == 1


def test_quote_source_serves_fresh_blocks_stale(tmp_path: Path) -> None:
    _write_latest(tmp_path / "cdna_crypto_latest.json", [_row()])
    src = CdnaFastQuoteSource(timeseries_dir=tmp_path, max_age_seconds=60, clock=lambda: NOW)
    leg = {"platform": "cdna", "side": "YES", "market_id_or_ticker": "CDNA-BTC-1", "contract_id": "CID1"}
    q = src.quote(leg=leg, now=NOW)
    assert q["ask"] == 0.30 and "cdna_manual_fill_first_no_live_quote" in q["hard_blockers"]
    # rewrite with a stale quote, reload-on-change, then the same leg has NO ask + stale blocker.
    _write_latest(tmp_path / "cdna_crypto_latest.json", [_row(quote_timestamp=STALE_QTS)])
    src.reload_if_changed(NOW, force=True)
    q2 = src.quote(leg=leg, now=NOW)
    assert q2["ask"] is None and "cdna_snapshot_stale" in q2["hard_blockers"]
    diag = src.diagnostics(now=NOW)
    assert diag["cdna_supplied"] is True and diag["cdna_stale_rows"] == 1


# --- 7/8. no network/browser/order code; no secrets/.env --------------------- #
def test_no_network_browser_order_or_secret_code() -> None:
    src = Path("relative_value/cdna_fast_snapshot.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    for pat in (r"\burllib\b", r"\brequests\b", r"\bhttpx\b", r"\bsocket\b", r"\burlopen\b", r"\bhttp[s]?://",
                r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b", r"\bchromium\b",
                r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bcreate_order\b",
                r"\bgetenv\b", r"\bdotenv\b", r"\.env\b", r"\bapi_key\b", r"\bAuthorization\b",
                r"\bprivate_key\b", r"\bwallet\b"):
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat}"
