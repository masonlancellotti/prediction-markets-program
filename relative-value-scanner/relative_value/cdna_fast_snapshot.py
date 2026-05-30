"""CDNA latest-snapshot support for the crypto fast path (file-based, read-only).

CDNA (crypto.com prediction / display-price venue) is brought INTO the arb scan as
a *display-price, fill-first* leg only — never a server-side executable quote, never
a strict pre-fill arb. This module:

  * loads the latest saved CDNA snapshot (``cdna_crypto_latest.json``), reloading it
    when the file changes — it NEVER touches the network or a browser;
  * applies strict freshness gates (quote timestamp age, target not expired);
  * matches CDNA over-strike contracts to Kalshi/Polymarket partners by *payoff
    grammar*, not interval length — a 20m or 2h CDNA terminal-threshold contract can
    match a 1h/2h/4h partner at the SAME ``target_instant_utc`` (directional/up-down
    contracts still require identical ``reference_start_utc`` + ``interval_length``);
  * generates ``CDNA_FILL_FIRST`` candidates (4 over-strike patterns), size-capped by
    ``cdna_operator_size_cap``, with the required hard blockers / soft assumptions.

No trading, no order placement, no CDNA browser automation, no live CDNA execution,
no credentials, no ``.env``.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CDNA_PAPER_CANDIDATE_CLASS = "CDNA_FILL_FIRST"
CDNA_CANDIDATE_ACTION = "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
CDNA_CANDIDATE_TYPE = "CDNA_FILL_FIRST"
LATEST_FILENAME = "cdna_crypto_latest.json"
DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 60.0
SECONDS_20M = 1200
SECONDS_2H = 7200

# Soft assumptions surfaced for CDNA candidates in standard/aggressive risk modes.
CDNA_SOFT_ASSUMPTIONS = [
    "source_index_mismatch", "cdna_display_price_only", "cdna_executable_size_unverified",
    "cdna_no_orderbook_depth", "cdna_no_server_side_quote",
]

# (cdna_side, partner_side, synthetic_partner) — the 4 over-strike fill-first patterns.
_PATTERNS = [
    ("YES", "NO", False),   # 1. CDNA YES above K + partner NO above K
    ("NO", "YES", False),   # 2. CDNA NO above K + partner YES above K
    ("YES", "NO", True),    # 3. CDNA YES above K + synthetic partner NOT-above K
    ("NO", "YES", True),    # 4. CDNA NO above K + synthetic partner ABOVE K
]


# --------------------------------------------------------------------------- #
# loading (reload-on-change, file only)                                       #
# --------------------------------------------------------------------------- #
def resolve_latest_path(*, timeseries_dir: Any = None, evidence_dir: Any = None,
                        latest_path: Any = None) -> Path | None:
    if latest_path:
        return Path(latest_path)
    if timeseries_dir:
        return Path(timeseries_dir) / LATEST_FILENAME
    if evidence_dir:
        return Path(evidence_dir) / LATEST_FILENAME
    return None


def load_latest_cdna_snapshot(*, timeseries_dir: Any = None, evidence_dir: Any = None,
                              latest_path: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Load the latest CDNA snapshot file. Never raises; reports ``missing_reason``."""
    now = _now(now)
    path = resolve_latest_path(timeseries_dir=timeseries_dir, evidence_dir=evidence_dir, latest_path=latest_path)
    out: dict[str, Any] = {
        "loaded": False, "cdna_supplied": False, "snapshot_path": str(path) if path else None,
        "rows": [], "rows_loaded": 0, "generated_at": None, "cdna_snapshot_loaded_at": now.isoformat(),
        "file_mtime": None, "missing_reason": None,
    }
    if path is None:
        out["missing_reason"] = "cdna_timeseries_dir_not_provided"
        return out
    if not path.exists():
        out["missing_reason"] = "cdna_latest_file_not_found"
        return out
    try:
        out["file_mtime"] = path.stat().st_mtime
    except OSError:
        pass
    data = _read_json(path)
    if not isinstance(data, dict):
        out["missing_reason"] = "cdna_latest_file_unreadable"
        return out
    rows = [r for r in (data.get("contracts") or []) if isinstance(r, dict)]
    out.update({"loaded": True, "rows": rows, "rows_loaded": len(rows),
                "generated_at": data.get("generated_at"), "cdna_supplied": bool(rows)})
    if not rows:
        out["missing_reason"] = "cdna_latest_file_empty"
    return out


class CdnaFastQuoteSource:
    """Reload-on-change CDNA quote source for the hot loop (file only, no network).

    Serves a CDNA leg's display price as its ``ask`` (no slippage, no depth beyond the
    operator size cap), with freshness blockers. ``reload_if_changed`` re-reads the
    latest file only when its mtime changes — never per leg, never over the network.
    """

    def __init__(self, *, timeseries_dir: Any = None, evidence_dir: Any = None, latest_path: Any = None,
                 max_age_seconds: float = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS, clock: Callable[[], datetime] | None = None) -> None:
        self._kwargs = {"timeseries_dir": timeseries_dir, "evidence_dir": evidence_dir, "latest_path": latest_path}
        self.max_age_seconds = float(max_age_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.reloads = 0
        self._by_contract: dict[str, dict[str, Any]] = {}
        self.snapshot: dict[str, Any] = {}
        self.reload_if_changed(self._clock(), force=True)

    def reload_if_changed(self, now: datetime | None = None, *, force: bool = False) -> bool:
        now = _now(now or self._clock())
        path = resolve_latest_path(**self._kwargs)
        mtime = None
        if path is not None and path.exists():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = None
        if not force and mtime == self.snapshot.get("file_mtime"):
            return False
        self.snapshot = load_latest_cdna_snapshot(now=now, **self._kwargs)
        self._by_contract = {}
        for row in self.snapshot.get("rows") or []:
            for key in _row_lookup_keys(row):
                self._by_contract.setdefault(key, row)
        self.reloads += 1
        return True

    def quote(self, *, leg: dict[str, Any], now: datetime) -> dict[str, Any]:
        now = _now(now)
        side = str(leg.get("side") or "").upper()
        base = {
            "platform": "cdna", "market_id_or_ticker": leg.get("market_id_or_ticker"),
            "token_id": leg.get("token_id"), "side": leg.get("side"),
            "bid": None, "ask": None, "bid_size": None, "ask_size": None,
            "quote_timestamp_utc": now.isoformat(), "quote_timestamp": now.isoformat(),
            "quote_age_ms": None, "source": "cdna_display_reference_snapshot",
            "depth_status": "display_price_only", "complement_quote_used": False,
            "cdna_snapshot_loaded_at": self.snapshot.get("cdna_snapshot_loaded_at"),
            "hard_blockers": ["cdna_manual_fill_first_no_live_quote"],
        }
        row = self._lookup(leg)
        if row is None:
            base["hard_blockers"].append("missing_cdna_snapshot_row")
            base["hard_blockers"].append(_missing_display_label(side))
            return base
        fresh = evaluate_cdna_row_freshness(row, now=now, max_age_seconds=self.max_age_seconds)
        base["quote_age_ms"] = fresh["quote_age_ms"]
        base["cdna_quote_age_ms"] = fresh["quote_age_ms"]
        base["quote_timestamp"] = row.get("quote_timestamp") or base["quote_timestamp"]
        price = _cdna_display_price(row, side)
        if price is None:
            base["hard_blockers"].append(_missing_display_label(side))
        if not fresh["fresh"]:
            base["hard_blockers"].extend(fresh["blockers"])  # cdna_snapshot_stale / expired / missing
            return base  # stale/expired -> NO ask; CDNA-involved candidate is excluded
        base["ask"] = price
        base["depth_status"] = "display_price_only_fresh"
        return base

    def _lookup(self, leg: dict[str, Any]) -> dict[str, Any] | None:
        for key in _leg_lookup_keys(leg):
            if key in self._by_contract:
                return self._by_contract[key]
        return None

    def diagnostics(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = _now(now)
        part = partition_cdna_rows(self.snapshot.get("rows") or [], now=now, max_age_seconds=self.max_age_seconds)
        ages = [r["_freshness"]["quote_age_seconds"] for r in part["fresh_rows"] + part["stale_rows"]
                if r["_freshness"].get("quote_age_seconds") is not None]
        return {
            "cdna_supplied": bool(self.snapshot.get("cdna_supplied")),
            "cdna_rows_loaded": int(self.snapshot.get("rows_loaded") or 0),
            "cdna_latest_snapshot_age_seconds": (round(min(ages), 3) if ages else None),
            "cdna_snapshot_loaded_at": self.snapshot.get("cdna_snapshot_loaded_at"),
            "cdna_stale_rows": len(part["stale_rows"]),
            "cdna_fresh_rows": len(part["fresh_rows"]),
            "cdna_missing_reason": self.snapshot.get("missing_reason"),
            "cdna_reloads_during_loop": self.reloads,
            "cdna_top_of_hour_rows": part["cdna_top_of_hour_rows"],
            "cdna_20m_top_of_hour_rows": part["cdna_20m_top_of_hour_rows"],
            "cdna_2h_rows": part["cdna_2h_rows"],
        }


# --------------------------------------------------------------------------- #
# freshness                                                                   #
# --------------------------------------------------------------------------- #
def evaluate_cdna_row_freshness(row: dict[str, Any], *, now: datetime, max_age_seconds: float) -> dict[str, Any]:
    now = _now(now)
    blockers: list[str] = []
    qts = _parse_dt(row.get("quote_timestamp"))
    tgt = _parse_dt(row.get("target_instant_utc"))
    age = None if qts is None else (now - qts).total_seconds()
    if qts is None:
        blockers.append("missing_cdna_quote_timestamp")
    if not row.get("target_instant_utc"):
        blockers.append("missing_target_instant")
    elif tgt is not None and tgt <= now:
        blockers.append("cdna_target_expired")
    if age is not None and age > float(max_age_seconds):
        blockers.append("cdna_snapshot_stale")
    return {
        "fresh": not blockers,
        "quote_age_seconds": (round(age, 3) if age is not None else None),
        "quote_age_ms": (round(age * 1000.0, 3) if age is not None else None),
        "blockers": sorted(set(blockers)),
    }


def partition_cdna_rows(rows: list[dict[str, Any]], *, now: datetime, max_age_seconds: float) -> dict[str, Any]:
    fresh: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    toh = toh20 = h2 = 0
    for row in rows:
        f = evaluate_cdna_row_freshness(row, now=now, max_age_seconds=max_age_seconds)
        tagged = {**row, "_freshness": f}
        (fresh if f["fresh"] else stale).append(tagged)
        tgt = _parse_dt(row.get("target_instant_utc"))
        interval = _int(row.get("interval_length_seconds"))
        if tgt is not None and tgt.minute == 0 and tgt.second == 0:
            toh += 1
            if interval == SECONDS_20M:
                toh20 += 1
        if interval == SECONDS_2H:
            h2 += 1
    return {"fresh_rows": fresh, "stale_rows": stale,
            "cdna_top_of_hour_rows": toh, "cdna_20m_top_of_hour_rows": toh20, "cdna_2h_rows": h2}


# --------------------------------------------------------------------------- #
# harmonic payoff-grammar matching                                            #
# --------------------------------------------------------------------------- #
def payoff_grammar_match(cdna: dict[str, Any], partner: dict[str, Any], *, strike_tolerance: float = 0.0) -> dict[str, Any]:
    """Match CDNA to a partner by payoff grammar.

    terminal_threshold / point_in_time_at_target: match on asset + target_instant_utc +
    compatible strike + comparator — ``interval_length_seconds`` is metadata, NOT a
    blocker. directional_return / up_down: require identical reference_start_utc AND
    interval_length_seconds.
    """
    blockers: list[str] = []
    if _asset(cdna) != _asset(partner):
        blockers.append("asset_mismatch")

    fam_c = _family(cdna)
    fam_p = _family(partner)
    both_terminal = fam_c == "terminal_threshold" and fam_p == "terminal_threshold"
    if fam_c != fam_p and not both_terminal:
        blockers.append("incompatible_contract_family")

    if both_terminal:
        if not cdna.get("target_instant_utc") or str(cdna.get("target_instant_utc")) != str(partner.get("target_instant_utc")):
            blockers.append("target_time_mismatch")
    else:
        # directional_return / up_down — interval and window must match exactly.
        if not cdna.get("reference_start_utc") or str(cdna.get("reference_start_utc")) != str(partner.get("reference_start_utc")):
            blockers.append("target_time_mismatch")
        if _int(cdna.get("interval_length_seconds")) != _int(partner.get("interval_length_seconds")):
            blockers.append("incompatible_contract_family")

    ks = _f(cdna.get("threshold_or_strike"))
    kp = _f(partner.get("threshold_or_strike") if partner.get("threshold_or_strike") is not None else partner.get("strike"))
    if ks is None or kp is None or abs(ks - kp) > float(strike_tolerance):
        blockers.append("threshold_grid_mismatch")

    comp_c = str(cdna.get("comparator") or "above").lower()
    comp_p = str(partner.get("comparator") or "above").lower()
    if comp_c != comp_p:
        blockers.append("comparator_mismatch")

    return {
        "match": not blockers, "blockers": sorted(set(blockers)),
        "interval_length_ignored_for_terminal_threshold": both_terminal,
        "matched_on_target_instant_utc": cdna.get("target_instant_utc") if both_terminal else None,
    }


# --------------------------------------------------------------------------- #
# CDNA_FILL_FIRST candidate generation                                        #
# --------------------------------------------------------------------------- #
def build_cdna_fill_first_candidates(
    *, cdna_rows: list[dict[str, Any]], partner_legs: list[dict[str, Any]], now: datetime,
    max_age_seconds: float = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS, cdna_operator_size_cap: float = 1.0,
    operator_risk_mode: str = "aggressive", require_fresh: bool = True, min_net_edge: float = 0.0,
) -> dict[str, Any]:
    now = _now(now)
    part = partition_cdna_rows(cdna_rows, now=now, max_age_seconds=max_age_seconds)
    usable = part["fresh_rows"] if require_fresh else (part["fresh_rows"] + part["stale_rows"])

    candidates: list[dict[str, Any]] = []
    considered = 0
    matches = 0
    match_blockers: Counter[str] = Counter()
    by_instant: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for cdna in usable:
        for partner in partner_legs:
            considered += 1
            m = payoff_grammar_match(cdna, partner)
            if not m["match"]:
                match_blockers.update(m["blockers"])
                continue
            matches += 1
            for cdna_side, partner_side, synthetic in _PATTERNS:
                cand = _build_candidate(
                    cdna=cdna, partner=partner, cdna_side=cdna_side, partner_side=partner_side,
                    synthetic=synthetic, now=now, cap=float(cdna_operator_size_cap),
                    risk_mode=operator_risk_mode, min_net_edge=float(min_net_edge),
                )
                if cand["candidate_id"] in seen_ids:
                    continue
                seen_ids.add(cand["candidate_id"])
                candidates.append(cand)
                by_instant[str(cdna.get("target_instant_utc"))] += 1

    return {
        "candidates": candidates,
        "cdna_candidates_considered": considered,
        "cdna_fill_first_candidates": len(candidates),
        "cdna_terminal_threshold_matches": matches,
        "cdna_threshold_match_blockers": dict(match_blockers),
        "cdna_fill_first_candidates_by_instant": dict(by_instant),
        "cdna_stale_rows": len(part["stale_rows"]),
        "cdna_fresh_rows": len(part["fresh_rows"]),
        "cdna_top_of_hour_rows": part["cdna_top_of_hour_rows"],
        "cdna_20m_top_of_hour_rows": part["cdna_20m_top_of_hour_rows"],
        "cdna_2h_rows": part["cdna_2h_rows"],
    }


def _build_candidate(*, cdna, partner, cdna_side, partner_side, synthetic, now, cap, risk_mode, min_net_edge) -> dict[str, Any]:
    cdna_price = _cdna_display_price(cdna, cdna_side)
    cdna_fee = _cdna_fee(cdna)
    partner_ask = _f(partner.get("ask"))
    partner_fee = _f(partner.get("fee")) or 0.0
    target = cdna.get("target_instant_utc")
    strike = _f(cdna.get("threshold_or_strike"))

    cdna_leg = {
        "platform": "cdna", "side": cdna_side,
        "market_id_or_ticker": cdna.get("symbol") or cdna.get("contract_id"),
        "contract_id": cdna.get("contract_id"), "condition_id": None, "token_id": None,
        "threshold_or_strike": strike, "comparator": cdna.get("comparator") or "above",
        "ask": cdna_price, "fee": cdna_fee, "all_in_cost": (None if cdna_price is None else round(cdna_price + cdna_fee, 8)),
        "available_size_or_cap": cap, "market_shape": "point_in_time_threshold",
        "contract_family": "terminal_threshold", "payoff_observation_type": "point_in_time_at_target",
        "source_index": "cdna_display", "depth_status": "display_price_only",
        "target_instant_utc": target, "reference_start_utc": cdna.get("reference_start_utc"),
        "interval_length_seconds": cdna.get("interval_length_seconds"),
    }
    partner_leg = {
        "platform": partner.get("platform"), "side": partner_side,
        "market_id_or_ticker": partner.get("market_id_or_ticker"),
        "contract_id": partner.get("contract_id"), "condition_id": partner.get("condition_id"),
        "token_id": (partner.get("token_id_no") if partner_side == "NO" else partner.get("token_id_yes")) or partner.get("token_id"),
        "threshold_or_strike": _f(partner.get("threshold_or_strike") if partner.get("threshold_or_strike") is not None else partner.get("strike")),
        "comparator": partner.get("comparator") or "above",
        "ask": partner_ask, "fee": partner_fee,
        "all_in_cost": (None if partner_ask is None else round(partner_ask + partner_fee, 8)),
        "available_size_or_cap": _f(partner.get("available_size_or_cap")),
        "market_shape": "point_in_time_threshold", "contract_family": "terminal_threshold",
        "source_index": partner.get("source_index"), "synthetic_complement_side": bool(synthetic),
        "target_instant_utc": partner.get("target_instant_utc"),
    }

    # Over-strike YES/NO at the same instant/strike pays exactly 1 in every terminal state.
    cdna_cost = cdna_leg["all_in_cost"]
    partner_cost = partner_leg["all_in_cost"]
    total_cost = None if (cdna_cost is None or partner_cost is None) else round(cdna_cost + partner_cost, 8)
    net_edge = None if total_cost is None else round(1.0 - total_cost, 8)

    hard_blockers: list[str] = []
    fr = cdna.get("_freshness") or evaluate_cdna_row_freshness(cdna, now=now, max_age_seconds=DEFAULT_MAX_SNAPSHOT_AGE_SECONDS)
    hard_blockers.extend(fr.get("blockers") or [])  # cdna_snapshot_stale / expired / missing ts
    if cdna_price is None:
        hard_blockers.append(f"missing_cdna_display_{cdna_side.lower()}")
    if partner_ask is None:
        hard_blockers.append(f"missing_partner_{partner_side.lower()}_ask")
    if net_edge is not None and net_edge <= 0:
        hard_blockers.append("no_positive_net_edge_after_fees")

    assumptions = list(CDNA_SOFT_ASSUMPTIONS) if str(risk_mode).lower() in ("standard", "aggressive") else []
    if synthetic:
        assumptions = assumptions + ["synthetic_partner_complement_side"]

    pattern = f"cdna{cdna_side}_partner{partner_side}{'_synth' if synthetic else ''}"
    candidate_id = f"CDNA::{_asset(cdna)}::{target}::{strike}::{pattern}::{partner.get('market_id_or_ticker')}"
    return {
        "candidate_id": candidate_id, "asset": _asset(cdna), "candidate_type": CDNA_CANDIDATE_TYPE,
        "paper_candidate_class": CDNA_PAPER_CANDIDATE_CLASS, "candidate_action": CDNA_CANDIDATE_ACTION,
        "cdna_pattern": pattern, "target_instant_utc": target,
        "iteration_timestamp": now.strftime("%Y%m%dT%H%M%SZ"),
        "payoff_vector": [1, 1], "min_payoff": 1.0,
        "net_edge_after_fees": net_edge, "adjusted_net_edge_after_fees": net_edge,
        "expected_net_edge_after_fees": net_edge, "expected_adjusted_net_edge_after_fees": net_edge,
        "total_cost_after_fees": total_cost,
        "assumptions_accepted": assumptions, "source_indexes": ["cdna_display", partner.get("source_index") or "partner"],
        "requires_short_or_sell": False, "tradable_buy_only": True,
        "paper_candidate": bool(net_edge is not None and net_edge > 0 and not hard_blockers),
        "hard_blockers": sorted(set(hard_blockers)),
        "interval_length_ignored_for_terminal_threshold": True,
        "legs": [_universe_leg(cdna_leg), _universe_leg(partner_leg)],
        "basket_legs": [cdna_leg, partner_leg],
    }


def _universe_leg(leg: dict[str, Any]) -> dict[str, Any]:
    return {
        "leg_key": _leg_key(leg), "platform": leg.get("platform"),
        "market_id_or_ticker": leg.get("market_id_or_ticker"), "side": leg.get("side"),
        "token_id": leg.get("token_id"), "contract_id": leg.get("contract_id"),
        "condition_id": leg.get("condition_id"), "strike": leg.get("threshold_or_strike"),
        "reference_ask": _f(leg.get("ask")), "fee": _f(leg.get("fee")) or 0.0,
        "available_size_or_cap": _f(leg.get("available_size_or_cap")),
    }


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _row_lookup_keys(row: dict[str, Any]) -> list[str]:
    keys = []
    if row.get("contract_id"):
        keys.append(f"cid::{row['contract_id']}")
    if row.get("symbol"):
        keys.append(f"sym::{row['symbol']}")
    return keys


def _leg_lookup_keys(leg: dict[str, Any]) -> list[str]:
    keys = []
    if leg.get("contract_id"):
        keys.append(f"cid::{leg['contract_id']}")
    if leg.get("market_id_or_ticker"):
        keys.append(f"sym::{leg['market_id_or_ticker']}")
        keys.append(f"cid::{leg['market_id_or_ticker']}")
    return keys


def _cdna_display_price(row: dict[str, Any], side: str) -> float | None:
    return _f(row.get("display_yes")) if str(side).upper().endswith("YES") else _f(row.get("display_no"))


def _cdna_fee(row: dict[str, Any]) -> float:
    return round((_f(row.get("exchange_fee")) or 0.0) + (_f(row.get("technology_fee")) or 0.0), 8)


def _missing_display_label(side: str) -> str:
    return "missing_cdna_display_yes" if str(side).upper().endswith("YES") else "missing_cdna_display_no"


def _family(obj: dict[str, Any]) -> str:
    fam = str(obj.get("contract_family") or "").lower().strip()
    if fam:
        return fam
    shape = str(obj.get("market_shape") or "").lower()
    return "terminal_threshold" if shape == "point_in_time_threshold" else (shape or "unknown")


def _asset(obj: dict[str, Any]) -> str:
    return str(obj.get("asset") or "").upper()


def _leg_key(leg: dict[str, Any]) -> str:
    return f"{str(leg.get('platform') or '').lower()}::{leg.get('market_id_or_ticker') or ''}::{str(leg.get('side') or '').upper()}"


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now(now: datetime | None) -> datetime:
    ts = now or datetime.now(timezone.utc)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _f(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    f = _f(value)
    return None if f is None else int(f)
