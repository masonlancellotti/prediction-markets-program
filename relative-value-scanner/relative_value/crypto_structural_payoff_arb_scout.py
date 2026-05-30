"""Structural payoff-state arb engine for crypto interval markets.

Converts every compatible contract for the same asset + ``target_instant_utc``
into a payoff vector over discrete terminal price states, then searches for
structural opportunities that direct same-key matching misses:

  - LONG_ONLY_GUARANTEED_PAYOFF (buy-only basket that always pays >= $1 for < $1)
  - BUCKET_TO_CUMULATIVE_THRESHOLD (Kalshi YES buckets synthesize a threshold)
  - CROSS_VENUE_THRESHOLD_BASIS (same strike/instant, different source index)
  - SAME_PAYOFF_CHEAPER_BASKET (identical payoff vector, cheaper cost)
  - MONOTONICITY_VIOLATION / THRESHOLD_TO_BUCKET_DIAGNOSTIC (diagnostic-only)
  - UP_DOWN_SAME_WINDOW (start->end change, same reference_start + target)

Hard guarantees (identical to the rest of the crypto stack):
  - Public-read-only / saved-evidence only. No order/cancel/account/auth/wallet/
    browser/proxy code. CDNA is never fetched.
  - Synthetic cumulative events are built from YES on mutually-exclusive buckets
    only — never NO on many buckets (that is not a $1 payoff).
  - Asks only; NO midpoint. ``net_edge_after_fees`` is the only edge a candidate
    decision uses. Target-instant mismatch stays a hard blocker. CDNA stays
    display-price/fill-first (never strict pre-fill arb).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from relative_value.crypto_contract_grammar import (
    CONTRACT_FAMILY_BARRIER_TOUCH,
    CONTRACT_FAMILY_DIRECTIONAL_RETURN,
    CONTRACT_FAMILY_TERMINAL_RANGE,
    CONTRACT_FAMILY_TERMINAL_THRESHOLD,
    CONTRACT_FAMILY_UNKNOWN,
    TERMINAL_FAMILIES,
    classify_contract_family,
    normalize_contract_row,
)
from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    ACTION_IGNORE,
    ACTION_PAPER,
    ACTION_WATCH,
    CLASS_CDNA,
    CLASS_NONE,
    CLASS_OPERATOR,
    CLASS_STRICT,
    collect_hard_blockers,
    normalize_operator_risk_mode,
)


HttpGet = Callable[[str, float], Any]
Sleep = Callable[[float], None]

SCHEMA_KIND = "crypto_structural_payoff_arb_scout_v1"
SCHEMA_VERSION = 1

DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
CDNA_FEE_PER_CONTRACT = 0.02
DEFAULT_MAX_BASKET_LEGS = 12
DEFAULT_MAX_QUOTE_AGE_SECONDS = 300.0
DEFAULT_MIN_AVAILABLE_NOTIONAL = 1.0
DEFAULT_CDNA_OPERATOR_SIZE_CAP = 1.0

# Candidate types.
CT_LONG_ONLY = "LONG_ONLY_GUARANTEED_PAYOFF"
CT_SAME_PAYOFF_CHEAPER = "SAME_PAYOFF_CHEAPER_BASKET"
CT_BUCKET_TO_THRESHOLD = "BUCKET_TO_CUMULATIVE_THRESHOLD"
CT_CROSS_VENUE = "CROSS_VENUE_THRESHOLD_BASIS"
CT_MONOTONICITY = "MONOTONICITY_VIOLATION"
CT_MONOTONICITY_COVER = "THRESHOLD_MONOTONICITY_COVER"
CT_THRESHOLD_TO_BUCKET = "THRESHOLD_TO_BUCKET_DIAGNOSTIC"
CT_UP_DOWN = "UP_DOWN_SAME_WINDOW"
CT_BARRIER = "BARRIER_TOUCH_DIAGNOSTIC"

# Per-(asset, instant) cap on each sampled, non-positive guaranteed-cover bucket
# (missing-quote / synthetic / net-negative). Bounds output on thin books while
# still surfacing the best cross-venue & long-only near-misses, not just covers.
_G2_SAMPLE_CAP = 25

# Canonical buy-only / diagnostic candidate classes for generation-coverage.
CANDIDATE_CLASSES = (
    CT_UP_DOWN,
    CT_MONOTONICITY_COVER,
    CT_LONG_ONLY,
    CT_CROSS_VENUE,
    CT_BUCKET_TO_THRESHOLD,
    "CDNA_FILL_FIRST",
    CT_SAME_PAYOFF_CHEAPER,
    "DIAGNOSTIC_ONLY_REQUIRES_SHORT",
)

QUOTE_COVERAGE_SIDE_KEYS = (
    "kalshi_yes_ask_present",
    "kalshi_no_ask_present",
    "kalshi_yes_bid_present",
    "kalshi_no_bid_present",
    "polymarket_yes_ask_present",
    "polymarket_no_ask_present",
    "polymarket_yes_bid_present",
    "polymarket_no_bid_present",
    "cdna_display_yes_present",
    "cdna_display_no_present",
)

QUOTE_COVERAGE_EXTRA_KEYS = (
    "complement_quote_used_count",
    "complement_quote_possible_but_missing_bid",
    "explicit_ask_used_count",
    "gamma_top_of_book_fallback_count",
    "clob_book_used_count",
)

_KALSHI_FEE = KalshiTieredFeeModel()
_POLY_FEE = PolymarketConservativeFeeModel()

_PRICE_STATE_OBS = {"point_in_time_at_target", "range_at_target"}

# Mason cannot short. A row that needs selling/shorting is never a tradable
# candidate — it is diagnostic-only and must not pollute the actionable buy-only
# blocker dashboard.
_REQUIRES_SHORT_BLOCKERS = {"requires_short_or_not_guaranteed", "threshold_to_bucket_requires_short"}

# Blocker buckets for the separated dashboards. The three buckets are kept
# DISJOINT so the tables don't double-count: "actionable" = a fresh/complete
# quote could flip the row tradable; "economic" = it priced but lost edge to
# fees/basis; "diagnostic" = structurally non-tradable for a buy-only operator.
ECONOMIC_REJECTION_BLOCKERS = {
    "no_positive_net_edge_after_fees",
    "no_positive_adjusted_net_edge_after_basis_buffer",
}
ACTIONABLE_BUY_ONLY_BLOCKERS = {
    "missing_yes_lower_ask",
    "missing_no_higher_ask",
    "missing_lower_yes_ask",
    "missing_higher_no_ask",
    "missing_partner_yes_ask",
    "missing_partner_no_ask",
    "missing_partner_complement_ask",
    "missing_kalshi_yes_ask",
    "missing_kalshi_no_ask",
    "missing_polymarket_yes_ask",
    "missing_polymarket_no_ask",
    "missing_cdna_display_yes",
    "missing_cdna_display_no",
    "missing_bucket_leg_ask",
    "missing_cdna_display_price",
    "missing_ask",
    "missing_quote_depth",
    "insufficient_available_notional",
    "stale_or_missing_quote",
    "threshold_order_invalid",
}
DIAGNOSTIC_ONLY_NON_TRADABLE_REASONS = {
    "requires_short_or_not_guaranteed",
    "threshold_to_bucket_requires_short",
    "monotonicity_diagnostic_only",
    "barrier_vs_terminal_mismatch",
    "incompatible_shape",
}


# ---------------------------------------------------------------------------- #
# Instrument model                                                             #
# ---------------------------------------------------------------------------- #


@dataclass
class Leg:
    platform: str
    asset: str
    target_instant_utc: str
    market_shape: str
    payoff_observation_type: str
    side: str  # YES / NO / DISPLAY_YES / DISPLAY_NO
    ask: float | None
    fee: float | None
    all_in_cost: float | None
    available_size_or_cap: float | None
    source_index: str | None
    market_id_or_ticker: str | None
    hard_blockers: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    complement_used: bool = False
    complement_source: str | None = None
    # Identity / quote provenance carried for audit packs (read-only metadata).
    condition_id: str | None = None
    token_ids: dict[str, Any] = field(default_factory=dict)
    contract_id: str | None = None
    quote_timestamp: str | None = None
    depth_status: str | None = None

    def to_dict(self, payoff_vector: list[int] | None = None) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "asset": self.asset,
            "target_instant_utc": self.target_instant_utc,
            "market_shape": self.market_shape,
            "payoff_observation_type": self.payoff_observation_type,
            "side": self.side,
            "ask": self.ask,
            "fee": self.fee,
            "all_in_cost": self.all_in_cost,
            "available_size_or_cap": self.available_size_or_cap,
            "source_index": self.source_index,
            "market_id_or_ticker": self.market_id_or_ticker,
            "condition_id": self.condition_id,
            "token_ids": dict(self.token_ids or {}),
            "contract_id": self.contract_id,
            "quote_timestamp": self.quote_timestamp,
            "depth_status": self.depth_status,
            "payoff_vector": payoff_vector,
            "hard_blockers": list(self.hard_blockers),
            "risk_notes": list(self.risk_notes),
            "complement_used": bool(self.complement_used),
            "complement_source": self.complement_source,
        }


@dataclass
class Instrument:
    """A buy-only tradeable: a single market side, or a YES-only basket of legs."""

    key: str
    vector: tuple[int, ...]
    legs: list[Leg]
    leg_vectors: list[tuple[int, ...]]
    label: str

    @property
    def all_in_cost(self) -> float | None:
        if any(leg.all_in_cost is None for leg in self.legs):
            return None
        return round(sum(leg.all_in_cost for leg in self.legs), 8)

    @property
    def available_size_or_cap(self) -> float | None:
        sizes = [leg.available_size_or_cap for leg in self.legs if leg.available_size_or_cap is not None]
        if len(sizes) != len(self.legs):
            return None
        return min(sizes) if sizes else None

    @property
    def is_cdna(self) -> bool:
        return any(leg.platform == "cdna" for leg in self.legs)

    @property
    def platforms(self) -> set[str]:
        return {leg.platform for leg in self.legs}

    @property
    def source_indexes(self) -> set[str]:
        return {str(leg.source_index) for leg in self.legs if leg.source_index}

    def leg_blockers(self) -> list[str]:
        out: list[str] = []
        for leg in self.legs:
            out.extend(leg.hard_blockers)
        return out


# ---------------------------------------------------------------------------- #
# Public entry points                                                          #
# ---------------------------------------------------------------------------- #


def write_crypto_structural_payoff_arb_scout_files(
    *, json_output: Path, markdown_output: Path, **kwargs: Any
) -> dict[str, Any]:
    report = build_crypto_structural_payoff_arb_scout_report(**kwargs)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_crypto_structural_payoff_arb_scout_markdown(report), encoding="utf-8")
    return report


def build_crypto_structural_payoff_arb_scout_report(
    *,
    assets: list[str],
    evidence_roots: list[Path] | None = None,
    operator_risk_mode: str = "conservative",
    include_cdna: bool = False,
    operator_accept_cdna_display_price_risk: bool = False,
    allow_top_of_book_depth: bool = False,
    operator_size_cap: float = 0.0,
    cdna_operator_size_cap: float = DEFAULT_CDNA_OPERATOR_SIZE_CAP,
    cdna_evidence_dir: Path | None = None,
    cdna_timeseries_dir: Path | None = None,
    max_cdna_snapshot_age_seconds: float = 60.0,
    require_cdna_fresh_for_cdna_candidates: bool = True,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    max_basket_legs: int = DEFAULT_MAX_BASKET_LEGS,
    source_basis_buffer_bps: float = 0.0,
    source_basis_buffer_absolute: dict[str, float] | str | None = None,
    generated_at: datetime | None = None,
    refresh_kalshi_polymarket: bool = False,
    lookahead_hours: float = 8.0,
    http_get: "HttpGet | None" = None,
    sleep: "Sleep | None" = None,
    rows_by_asset: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    asset_list = [str(a).strip().upper() for a in assets if str(a).strip()]
    depth_permissive = bool(allow_top_of_book_depth and operator_size_cap and operator_size_cap > 0)
    basis_absolute = _parse_basis_absolute(source_basis_buffer_absolute)
    basis_buffer_edge = round(float(source_basis_buffer_bps) / 10000.0, 8)

    loaded, load_diag = _load_rows(
        asset_list=asset_list, evidence_roots=evidence_roots, refresh=refresh_kalshi_polymarket,
        include_cdna=include_cdna, cdna_evidence_dir=cdna_evidence_dir, lookahead_hours=lookahead_hours,
        generated=generated, http_get=http_get, sleep=sleep, rows_by_asset=rows_by_asset,
    )

    # CDNA latest-snapshot rows (file only; no network/browser). Fresh, non-expired
    # terminal-threshold rows are normalized into each asset's cdna_rows so they flow
    # through the same payoff engine as Kalshi/Polymarket; stale rows are excluded and
    # counted. This closes the CDNA_FILL_FIRST coverage gap (rows present, attempted=0).
    cdna_snapshot_diag = _load_and_inject_cdna_snapshot(
        loaded, asset_list=asset_list, include_cdna=include_cdna,
        cdna_timeseries_dir=cdna_timeseries_dir, cdna_evidence_dir=cdna_evidence_dir,
        max_age_seconds=float(max_cdna_snapshot_age_seconds),
        require_fresh=bool(require_cdna_fresh_for_cdna_candidates),
        size_cap=float(cdna_operator_size_cap), now=generated,
    )
    if cdna_snapshot_diag.get("cdna_rows_loaded"):
        load_diag["cdna_rows_loaded"] = int(cdna_snapshot_diag["cdna_rows_loaded"])
    load_diag["cdna_snapshot"] = cdna_snapshot_diag

    opts = _Opts(
        risk_mode=risk_mode, include_cdna=include_cdna,
        operator_accept_cdna=operator_accept_cdna_display_price_risk,
        depth_permissive=depth_permissive, operator_size_cap=float(operator_size_cap or 0.0),
        cdna_operator_size_cap=float(cdna_operator_size_cap), max_quote_age_seconds=float(max_quote_age_seconds),
        min_available_notional=float(min_available_notional), max_basket_legs=int(max_basket_legs),
        generated=generated, basis_buffer_edge=basis_buffer_edge, basis_absolute=basis_absolute,
    )

    rows: list[dict[str, Any]] = []
    state_grids: list[dict[str, Any]] = []
    grammar_counts: Counter = Counter()
    mono_diag: Counter = Counter()
    coverage: dict[str, Counter] = defaultdict(Counter)
    quote_coverage = _empty_quote_coverage_diagnostics()
    up_down_audit = _empty_up_down_audit()
    for asset in asset_list:
        rec = loaded.get(asset) or {}
        k = list(rec.get("kalshi_rows") or [])
        p = list(rec.get("polymarket_rows") or [])
        c = list(rec.get("cdna_rows") or []) if include_cdna else []
        for src in (k, p, c):
            for row in src:
                fam = _classify_row(row)
                row["contract_family"] = fam
                grammar_counts[fam] += 1
        _merge_quote_coverage(quote_coverage, _quote_coverage_diagnostics(k + p + c, opts=opts))
        _merge_up_down_audit(up_down_audit, _up_down_window_audit(asset, k + p + c))
        asset_rows, asset_grids, asset_mono = _scan_asset(asset=asset, kalshi=k, polymarket=p, cdna=c, opts=opts, coverage=coverage)
        rows.extend(asset_rows)
        state_grids.extend(asset_grids)
        mono_diag.update(asset_mono)

    # Dedup, normalize new fields on diagnostic rows, classify execution type
    # (only BUY_ONLY can be a paper candidate), then sort by adjusted edge.
    rows = _dedup_rows(rows)
    for r in rows:
        if "adjusted_net_edge_after_fees" not in r:
            r["adjusted_net_edge_after_fees"] = r.get("net_edge_after_fees")
        r.setdefault("comparability_tier", "DIAGNOSTIC_ONLY")
        r.setdefault("contract_family", CONTRACT_FAMILY_UNKNOWN)
        r.setdefault("source_basis_buffer", 0.0)
        execution_type, buy_only, requires_short = _classify_execution(r)
        r["candidate_execution_type"] = execution_type
        r["tradable_buy_only"] = buy_only
        r["requires_short_or_sell"] = requires_short
        if not buy_only and r.get("paper_candidate"):
            # Shorting is never a valid execution path -> never a paper candidate.
            r["paper_candidate"] = False
            r["paper_candidate_class"] = CLASS_NONE
            r["action"] = ACTION_WATCH if not (r.get("hard_blockers")) else ACTION_IGNORE
        r.setdefault("quote_side_diagnostics", [])
        _annotate_row_operability(r)
    rows.sort(
        key=lambda r: (1 if r.get("paper_candidate") else 0, _safe_float(r.get("adjusted_net_edge_after_fees"))),
        reverse=True,
    )

    _finish_up_down_audit(up_down_audit, rows)
    _finish_quote_coverage(quote_coverage, rows)
    candidate_generation_coverage = _finalize_coverage(coverage, rows)
    cdna_match_diag = _cdna_match_diagnostics(loaded, asset_list=asset_list, cdna_snapshot=cdna_snapshot_diag, now=generated)
    candidate_generation_coverage = _augment_cdna_coverage(
        candidate_generation_coverage, rows=rows, cdna_snapshot=cdna_snapshot_diag, match_diag=cdna_match_diag)
    cdna_participation = _build_cdna_participation(
        rows=rows, cdna_snapshot=cdna_snapshot_diag, match_diag=cdna_match_diag, include_cdna=include_cdna)
    summary = _summary(rows, state_grids)
    summary["contract_grammar_counts"] = dict(grammar_counts)
    summary["candidate_generation_coverage"] = candidate_generation_coverage
    summary["cdna_participation"] = cdna_participation
    summary["quote_coverage_diagnostics"] = quote_coverage
    summary["up_down_audit"] = up_down_audit
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "public_read_only": True,
        "saved_files_only": not refresh_kalshi_polymarket,
        "strict_exact_arb": False,
        "operator_risk_mode": risk_mode,
        "assets_requested": asset_list,
        "include_cdna": bool(include_cdna),
        "operator_accept_cdna_display_price_risk": bool(operator_accept_cdna_display_price_risk),
        "allow_top_of_book_depth": bool(allow_top_of_book_depth),
        "operator_size_cap": float(operator_size_cap or 0.0),
        "max_basket_legs": int(max_basket_legs),
        "source_basis_buffer_bps": float(source_basis_buffer_bps),
        "source_basis_buffer_edge": basis_buffer_edge,
        "source_basis_buffer_absolute": basis_absolute,
        "refresh_kalshi_polymarket": bool(refresh_kalshi_polymarket),
        "evidence_roots": [str(p) for p in (evidence_roots or [])],
        "load_diagnostics": load_diag,
        "state_grids": state_grids,
        "rows": rows,
        "summary_counts": summary,
        "contract_grammar_counts": summary.get("contract_grammar_counts", {}),
        "candidate_type_counts": summary["candidate_type_counts"],
        "comparability_tier_counts": summary["comparability_tier_counts"],
        "candidate_generation_coverage": candidate_generation_coverage,
        "cdna_participation": cdna_participation,
        "cdna_snapshot_diagnostics": cdna_snapshot_diag,
        "quote_coverage_diagnostics": quote_coverage,
        "up_down_audit": up_down_audit,
        "monotonicity_cover_diagnostics": {
            "monotonicity_pairs_checked": int(mono_diag.get("pairs_checked", 0)),
            "monotonicity_cover_candidates_generated": int(mono_diag.get("generated", 0)),
            "monotonicity_cover_paper_candidates": int(mono_diag.get("paper_candidates", 0)),
            "missing_yes_lower_ask": int(mono_diag.get("missing_yes_lower_ask", 0)),
            "missing_no_higher_ask": int(mono_diag.get("missing_no_higher_ask", 0)),
            "complement_quote_used": int(mono_diag.get("complement_quote_used", 0)),
        },
        "basis_buffer_sensitivity": _basis_buffer_sensitivity(rows, basis_buffer_edge),
        "top_blockers": summary["top_blockers"],
        "actionable_buy_only_blockers": summary["actionable_buy_only_blockers"],
        "diagnostic_only_non_tradable_reasons": summary["diagnostic_only_non_tradable_reasons"],
        "economic_rejections": summary["economic_rejections"],
        "diagnostic_only_short_required_rows": summary["diagnostic_only_short_required_rows"],
        "top_buy_only_near_misses": summary["top_buy_only_near_misses"],
        "monotonicity_covers_one_leg_missing": summary["monotonicity_covers_one_leg_missing"],
        "near_miss_threshold_buckets": summary["near_miss_threshold_buckets"],
        "quote_side_diagnostic_counts": summary["quote_side_diagnostic_counts"],
        "near_miss_buy_only_rows": summary["near_miss_buy_only_rows"],
        "manual_micro_test_candidate_rows": summary["manual_micro_test_candidate_rows"],
        "complement_quote_rows": summary["complement_quote_rows"],
        "cdna_fill_first_candidates": summary["cdna_fill_first_candidates"],
        "top_fee_drag_rows": summary["top_fee_drag_rows"],
        "safety": {
            "diagnostic_only": True,
            "public_read_only": True,
            "cdna_network_fetch_attempted": False,
            "uses_midpoint": False,
            "uses_asks_for_entry": True,
            "synthetic_uses_yes_buckets_only": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
            "strict_exact_arb": False,
        },
    }


@dataclass
class _Opts:
    risk_mode: str
    include_cdna: bool
    operator_accept_cdna: bool
    depth_permissive: bool
    operator_size_cap: float
    cdna_operator_size_cap: float
    max_quote_age_seconds: float
    min_available_notional: float
    max_basket_legs: int
    generated: datetime
    basis_buffer_edge: float = 0.0
    basis_absolute: dict[str, float] = field(default_factory=dict)


def _classify_row(row: dict[str, Any]) -> str:
    return classify_contract_family(
        payoff_observation_type=row.get("payoff_observation_type"),
        market_shape=row.get("market_shape"),
        comparator=row.get("comparator"),
        threshold_value=row.get("threshold_or_strike") if row.get("payoff_observation_type") == "point_in_time_at_target" else None,
        lower_bound=row.get("bucket_floor"),
        upper_bound=row.get("bucket_cap"),
        rules_text=row.get("rules_text"),
        title=row.get("market_id_or_ticker"),
    )


def _parse_basis_absolute(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {str(k).upper(): float(v) for k, v in value.items()}
    out: dict[str, float] = {}
    if isinstance(value, str):
        for part in value.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    out[k.strip().upper()] = float(v.strip())
                except ValueError:
                    continue
    return out


# ---------------------------------------------------------------------------- #
# Row loading (refresh via interval collector, or saved snapshots)             #
# ---------------------------------------------------------------------------- #


def _load_rows(
    *, asset_list, evidence_roots, refresh, include_cdna, cdna_evidence_dir, lookahead_hours,
    generated, http_get, sleep, rows_by_asset,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    diag: dict[str, Any] = {"source": None, "roots_read": [], "assets_loaded": []}
    if rows_by_asset is not None:
        diag["source"] = "in_memory"
        diag["assets_loaded"] = sorted(rows_by_asset.keys())
        return {str(k).upper(): v for k, v in rows_by_asset.items()}, diag

    if refresh:
        from relative_value.crypto_interval_evidence_collector import (  # noqa: WPS433
            write_crypto_interval_live_evidence,
        )

        summary = write_crypto_interval_live_evidence(
            assets=asset_list, output_root=None, lookahead_hours=lookahead_hours, generated_at=generated,
            http_get=http_get, cdna_evidence_dir=cdna_evidence_dir if include_cdna else None, sleep=sleep,
        )
        diag["source"] = "live_refresh"
        loaded = {str(r.get("asset")).upper(): r for r in summary.get("per_asset") or []}
        diag["assets_loaded"] = sorted(loaded.keys())
        # Keep the collector's per-asset warnings / clob failures so the watcher
        # can render a "Latest Iteration Errors" panel (purely diagnostic).
        diag["per_asset_diagnostics"] = [
            {
                "asset": r.get("asset"),
                "kalshi_diagnostics": (r.get("kalshi_diagnostics") or {}),
                "polymarket_diagnostics": (r.get("polymarket_diagnostics") or {}),
                "cdna_diagnostics": (r.get("cdna_diagnostics") or {}),
            }
            for r in summary.get("per_asset") or []
        ]
        return loaded, diag

    diag["source"] = "saved_evidence"
    loaded: dict[str, dict[str, Any]] = {}
    for root in evidence_roots or []:
        root = Path(root)
        diag["roots_read"].append(str(root))
        for asset in asset_list:
            if asset in loaded:
                continue
            snap = root / asset.lower() / "interval_typed_keys.json"
            if not snap.exists():
                continue
            try:
                payload = json.loads(snap.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                loaded[asset] = payload
    diag["assets_loaded"] = sorted(loaded.keys())
    return loaded, diag


def _load_and_inject_cdna_snapshot(
    loaded: dict[str, dict[str, Any]], *, asset_list, include_cdna, cdna_timeseries_dir, cdna_evidence_dir,
    max_age_seconds, require_fresh, size_cap, now,
) -> dict[str, Any]:
    """Load CDNA latest-snapshot rows (file only), freshness-gate, normalize fresh
    rows to the scout's terminal-threshold row schema, and inject them into each
    asset's ``cdna_rows`` so they flow through the same payoff engine. Stale/expired
    rows are excluded and counted. Returns CDNA snapshot diagnostics."""
    diag: dict[str, Any] = {
        "cdna_supplied": False, "cdna_rows_loaded": 0, "cdna_fresh_rows": 0, "cdna_stale_rows": 0,
        "cdna_injected_rows": 0, "blocked_missing_cdna_display": 0, "cdna_target_instants": [],
        "cdna_top_of_hour_rows": 0, "cdna_20m_top_of_hour_rows": 0, "cdna_2h_rows": 0, "cdna_missing_reason": None,
    }
    if not include_cdna or (cdna_timeseries_dir is None and cdna_evidence_dir is None):
        if include_cdna:
            diag["cdna_missing_reason"] = "cdna_timeseries_dir_not_provided"
        return diag
    from relative_value.cdna_fast_snapshot import load_latest_cdna_snapshot, partition_cdna_rows
    snap = load_latest_cdna_snapshot(timeseries_dir=cdna_timeseries_dir, evidence_dir=cdna_evidence_dir, now=now)
    rows = snap.get("rows") or []
    diag.update({"cdna_supplied": bool(snap.get("cdna_supplied")), "cdna_rows_loaded": len(rows),
                 "cdna_missing_reason": snap.get("missing_reason")})
    part = partition_cdna_rows(rows, now=now, max_age_seconds=float(max_age_seconds))
    diag.update({"cdna_fresh_rows": len(part["fresh_rows"]), "cdna_stale_rows": len(part["stale_rows"]),
                 "cdna_top_of_hour_rows": part["cdna_top_of_hour_rows"],
                 "cdna_20m_top_of_hour_rows": part["cdna_20m_top_of_hour_rows"], "cdna_2h_rows": part["cdna_2h_rows"]})
    usable = part["fresh_rows"] if require_fresh else (part["fresh_rows"] + part["stale_rows"])
    asset_set = {str(a).upper() for a in asset_list}
    instants: set[str] = set()
    injected = missing_display = 0
    for r in usable:
        asset = str(r.get("asset") or "").upper()
        if asset_set and asset not in asset_set:
            continue
        norm = _normalize_snapshot_cdna_row(r, size_cap=size_cap)
        if norm is None:
            missing_display += 1
            continue
        rec = loaded.setdefault(asset, {})
        existing = rec.setdefault("cdna_rows", [])
        sig = (norm.get("market_id_or_ticker"), norm.get("target_instant_utc"))
        if any((e.get("market_id_or_ticker"), e.get("target_instant_utc")) == sig for e in existing):
            continue  # already supplied by the collector; do not double-count
        existing.append(norm)
        injected += 1
        if norm.get("target_instant_utc"):
            instants.add(str(norm["target_instant_utc"]))
    diag.update({"cdna_injected_rows": injected, "blocked_missing_cdna_display": missing_display,
                 "cdna_target_instants": sorted(instants)})
    return diag


def _normalize_snapshot_cdna_row(row: dict[str, Any], *, size_cap: float) -> dict[str, Any] | None:
    """latest.json CDNA row -> scout terminal-threshold row (prices under ``quote``).

    The display prices become yes/no asks (the scout adds the CDNA fee); CDNA carries
    no depth, so ask sizes are the operator size cap. Returns None when both display
    prices are missing (counted as ``blocked_missing_cdna_display``)."""
    dy = _to_float(row.get("display_yes"))
    dn = _to_float(row.get("display_no"))
    if dy is None and dn is None:
        return None
    return {
        "asset": str(row.get("asset") or "").upper(), "platform": "cdna",
        "market_shape": "point_in_time_threshold", "payoff_observation_type": "point_in_time_at_target",
        "comparator": row.get("comparator") or "above", "threshold_or_strike": _to_float(row.get("threshold_or_strike")),
        "bucket_floor": None, "bucket_cap": None, "reference_start_utc": row.get("reference_start_utc"),
        "target_instant_utc": row.get("target_instant_utc"), "interval_length_seconds": row.get("interval_length_seconds"),
        "price_source": "cdna_display", "market_id_or_ticker": row.get("symbol") or row.get("contract_id"),
        "contract_family": CONTRACT_FAMILY_TERMINAL_THRESHOLD,
        "quote": {"yes_ask": dy, "yes_ask_size": float(size_cap), "no_ask": dn, "no_ask_size": float(size_cap),
                  "depth_status": "display_price_only", "quote_timestamp": row.get("quote_timestamp"),
                  "quote_diagnostics": [], "blockers_remaining": []},
    }


def _kp_terminal_partner_legs(kalshi: list[dict[str, Any]], polymarket: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in list(kalshi) + list(polymarket):
        fam = str(row.get("contract_family") or _classify_row(row))
        if fam != CONTRACT_FAMILY_TERMINAL_THRESHOLD:
            continue
        out.append({
            "platform": str(row.get("platform") or "").lower(), "asset": str(row.get("asset") or "").upper(),
            "target_instant_utc": row.get("target_instant_utc"), "threshold_or_strike": _to_float(row.get("threshold_or_strike")),
            "comparator": row.get("comparator") or "above", "contract_family": CONTRACT_FAMILY_TERMINAL_THRESHOLD,
            "reference_start_utc": row.get("reference_start_utc"), "interval_length_seconds": row.get("interval_length_seconds"),
        })
    return out


def _cdna_match_diagnostics(loaded, *, asset_list, cdna_snapshot, now) -> dict[str, Any]:
    """Harmonic match accounting: CDNA terminal-threshold rows vs Kalshi/Polymarket
    terminal-threshold partners (same target_instant_utc/strike; interval ignored).
    Pure diagnostic — the candidate ROWS still come from the scout's payoff engine."""
    considered = matches = 0
    blockers: Counter = Counter()
    by_instant: Counter = Counter()
    if not cdna_snapshot.get("cdna_injected_rows"):
        return {"cdna_candidates_considered": 0, "cdna_terminal_threshold_matches": 0,
                "cdna_threshold_match_blockers": {}, "cdna_fill_first_candidates_by_instant": {}}
    from relative_value.cdna_fast_snapshot import payoff_grammar_match
    for asset in asset_list:
        rec = loaded.get(asset) or {}
        cdna_rows = [r for r in (rec.get("cdna_rows") or []) if str(r.get("platform") or "").lower() == "cdna"]
        partners = _kp_terminal_partner_legs(rec.get("kalshi_rows") or [], rec.get("polymarket_rows") or [])
        for c in cdna_rows:
            cdna_m = {"asset": str(asset).upper(), "target_instant_utc": c.get("target_instant_utc"),
                      "threshold_or_strike": _to_float(c.get("threshold_or_strike")), "comparator": c.get("comparator") or "above",
                      "contract_family": CONTRACT_FAMILY_TERMINAL_THRESHOLD, "reference_start_utc": c.get("reference_start_utc"),
                      "interval_length_seconds": c.get("interval_length_seconds")}
            for p in partners:
                considered += 1
                m = payoff_grammar_match(cdna_m, p)
                if m["match"]:
                    matches += 1
                    by_instant[str(c.get("target_instant_utc"))] += 1
                else:
                    blockers.update(m["blockers"])
    return {"cdna_candidates_considered": considered, "cdna_terminal_threshold_matches": matches,
            "cdna_threshold_match_blockers": dict(blockers), "cdna_fill_first_candidates_by_instant": dict(by_instant)}


def _augment_cdna_coverage(cov_list, *, rows, cdna_snapshot, match_diag) -> list[dict[str, Any]]:
    """Make CDNA_FILL_FIRST coverage reflect attempted CDNA rows (so present-but-
    unmatched/all-stale shows attempted>0, not a false 'never attempted' gap) and add
    the CDNA-specific blocked_* breakdown."""
    rows_loaded = int(cdna_snapshot.get("cdna_rows_loaded") or 0)
    if rows_loaded <= 0:
        return cov_list
    entry = next((e for e in cov_list if e.get("candidate_class") == "CDNA_FILL_FIRST"), None)
    if entry is None:
        entry = {"candidate_class": "CDNA_FILL_FIRST", "attempted": 0, "generated": 0, "priced": 0,
                 "net_positive": 0, "paper_candidate": 0, "paper": 0, "blocked_missing_ask": 0,
                 "blocked_stale": 0, "blocked_no_positive_net": 0, "blocked_shape_or_time": 0}
        cov_list.append(entry)
    mb = match_diag.get("cdna_threshold_match_blockers") or {}
    entry["attempted"] = max(int(entry.get("attempted", 0)), int(entry.get("generated", 0)), rows_loaded)
    entry["blocked_stale"] = int(entry.get("blocked_stale", 0)) + int(cdna_snapshot.get("cdna_stale_rows") or 0)
    entry["blocked_missing_cdna_display"] = int(cdna_snapshot.get("blocked_missing_cdna_display") or 0)
    entry["blocked_target_time_mismatch"] = int(mb.get("target_time_mismatch", 0))
    entry["blocked_threshold_grid_mismatch"] = int(mb.get("threshold_grid_mismatch", 0))
    entry.setdefault("blocked_no_positive_net", 0)
    return cov_list


def _build_cdna_participation(*, rows, cdna_snapshot, match_diag, include_cdna) -> dict[str, Any]:
    cdna_rows_with_leg = [
        r for r in rows
        if str(r.get("paper_candidate_class") or "") == CLASS_CDNA
        or any(str(l.get("platform") or "").lower() == "cdna" for l in r.get("basket_legs") or [])
    ]
    return {
        "cdna_supplied": bool(cdna_snapshot.get("cdna_supplied")) or bool(cdna_rows_with_leg),
        "cdna_rows_loaded": int(cdna_snapshot.get("cdna_rows_loaded") or 0),
        "cdna_fresh_rows": int(cdna_snapshot.get("cdna_fresh_rows") or 0),
        "cdna_stale_rows": int(cdna_snapshot.get("cdna_stale_rows") or 0),
        "cdna_target_instants": list(cdna_snapshot.get("cdna_target_instants") or []),
        "cdna_candidates_considered": int(match_diag.get("cdna_candidates_considered") or 0) or len(cdna_rows_with_leg),
        "cdna_terminal_threshold_matches": int(match_diag.get("cdna_terminal_threshold_matches") or 0),
        "cdna_fill_first_candidates": sum(1 for r in cdna_rows_with_leg if r.get("paper_candidate")),
        "cdna_fill_first_candidate_rows": len(cdna_rows_with_leg),
        "cdna_candidate_types_generated": dict(Counter(
            str(r.get("candidate_type")) for r in cdna_rows_with_leg if r.get("candidate_type"))),
        "cdna_fill_first_blockers": match_diag.get("cdna_threshold_match_blockers") or {},
        "cdna_fill_first_candidates_by_instant": match_diag.get("cdna_fill_first_candidates_by_instant") or {},
        "cdna_missing_reason": cdna_snapshot.get("cdna_missing_reason"),
    }


def _empty_quote_coverage_diagnostics() -> dict[str, Any]:
    return {
        "raw": {key: 0 for key in QUOTE_COVERAGE_SIDE_KEYS},
        "usable": {key: 0 for key in QUOTE_COVERAGE_SIDE_KEYS},
        **{key: 0 for key in QUOTE_COVERAGE_EXTRA_KEYS},
    }


def _quote_coverage_diagnostics(rows: list[dict[str, Any]], *, opts: "_Opts") -> dict[str, Any]:
    out = _empty_quote_coverage_diagnostics()
    for row in rows:
        platform = str(row.get("platform") or "").lower()
        quote = row.get("quote") or {}
        stale = _stale(row, opts.generated, opts.max_quote_age_seconds)
        diagnostics = quote.get("quote_diagnostics") or []
        depth_status = str(quote.get("depth_status") or "").lower()
        if "gamma_top_of_book_fallback_used" in diagnostics or "gamma_top_of_book_fallback" in depth_status:
            out["gamma_top_of_book_fallback_count"] += 1
        if "clob" in depth_status or "orderbook" in depth_status:
            out["clob_book_used_count"] += 1
        for side in ("yes", "no"):
            ask_key = f"{side}_ask"
            bid_key = f"{side}_bid"
            raw_ask = _quote_metric_key(platform, side, "ask")
            raw_bid = _quote_metric_key(platform, side, "bid")
            if raw_ask and _to_float(quote.get(ask_key)) is not None:
                out["raw"][raw_ask] += 1
                if _valid_ask(quote.get(ask_key)) is not None and not stale:
                    out["usable"][raw_ask] += 1
            if raw_bid and _to_float(quote.get(bid_key)) is not None:
                out["raw"][raw_bid] += 1
                if _valid_bid(quote.get(bid_key)) is not None and not stale:
                    out["usable"][raw_bid] += 1
            if _valid_ask(quote.get(ask_key)) is None:
                opposite_bid = quote.get("no_bid" if side == "yes" else "yes_bid")
                if _valid_bid(opposite_bid) is None:
                    out["complement_quote_possible_but_missing_bid"] += 1
    return out


def _quote_metric_key(platform: str, side: str, kind: str) -> str | None:
    if platform == "cdna":
        if kind == "ask":
            return f"cdna_display_{side}_present"
        return None
    if platform in {"kalshi", "polymarket"}:
        return f"{platform}_{side}_{kind}_present"
    return None


def _merge_quote_coverage(target: dict[str, Any], source: dict[str, Any]) -> None:
    for bucket in ("raw", "usable"):
        for key, value in (source.get(bucket) or {}).items():
            target[bucket][key] = int(target[bucket].get(key, 0)) + int(value or 0)
    for key in QUOTE_COVERAGE_EXTRA_KEYS:
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0) or 0)


def _finish_quote_coverage(coverage: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("complement_quote_used"):
            coverage["complement_quote_used_count"] += 1
        for leg in row.get("basket_legs") or []:
            if leg.get("complement_used"):
                coverage["complement_quote_used_count"] += 1
            elif _to_float(leg.get("ask")) is not None:
                coverage["explicit_ask_used_count"] += 1


def _empty_up_down_audit() -> dict[str, Any]:
    return {
        "up_down_kalshi_rows": 0,
        "up_down_polymarket_rows": 0,
        "up_down_exact_window_matches": 0,
        "up_down_candidates_generated": 0,
        "up_down_paper_candidates": 0,
        "sample_kalshi_windows": [],
        "sample_polymarket_windows": [],
        "top_post_fee_up_down_rows": [],
        "warning": "",
    }


def _up_down_window_audit(asset: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = _empty_up_down_audit()
    ud = [
        r for r in rows
        if str(r.get("payoff_observation_type")) == "interval_start_to_end_change"
        and r.get("target_instant_utc")
    ]
    by_platform = defaultdict(list)
    for row in ud:
        platform = str(row.get("platform") or "").lower()
        by_platform[platform].append(row)
        sample = {
            "asset": asset,
            "platform": platform,
            "reference_start_utc": row.get("reference_start_utc"),
            "target_instant_utc": row.get("target_instant_utc"),
            "interval_length_seconds": row.get("interval_length_seconds"),
            "market_id_or_ticker": row.get("market_id_or_ticker"),
        }
        if platform == "kalshi" and len(out["sample_kalshi_windows"]) < 5:
            out["sample_kalshi_windows"].append(sample)
        if platform == "polymarket" and len(out["sample_polymarket_windows"]) < 5:
            out["sample_polymarket_windows"].append(sample)
    out["up_down_kalshi_rows"] = len(by_platform.get("kalshi") or [])
    out["up_down_polymarket_rows"] = len(by_platform.get("polymarket") or [])
    for k in by_platform.get("kalshi") or []:
        for p in by_platform.get("polymarket") or []:
            if _up_down_exact_window_key(k) == _up_down_exact_window_key(p):
                out["up_down_exact_window_matches"] += 1
    return out


def _up_down_exact_window_key(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        row.get("reference_start_utc"),
        row.get("target_instant_utc"),
        row.get("interval_length_seconds"),
        row.get("payoff_observation_type"),
    )


def _merge_up_down_audit(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "up_down_kalshi_rows",
        "up_down_polymarket_rows",
        "up_down_exact_window_matches",
        "up_down_candidates_generated",
        "up_down_paper_candidates",
    ):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0) or 0)
    for key in ("sample_kalshi_windows", "sample_polymarket_windows"):
        target[key].extend((source.get(key) or [])[: max(0, 5 - len(target[key]))])


def _finish_up_down_audit(audit: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    updown = [r for r in rows if r.get("candidate_type") == CT_UP_DOWN]
    audit["up_down_candidates_generated"] = len(updown)
    audit["up_down_paper_candidates"] = sum(1 for r in updown if r.get("paper_candidate"))
    top = sorted(
        [r for r in updown if r.get("net_edge_after_fees") is not None],
        key=lambda r: _safe_float(r.get("net_edge_after_fees")),
        reverse=True,
    )
    audit["top_post_fee_up_down_rows"] = [
        {
            "asset": r.get("asset"),
            "target_instant_utc": r.get("target_instant_utc"),
            "reference_start_utc": r.get("reference_start_utc"),
            "net_edge_after_fees": r.get("net_edge_after_fees"),
            "adjusted_net_edge_after_fees": r.get("adjusted_net_edge_after_fees"),
            "hard_blockers": list(r.get("hard_blockers") or []),
            "paper_candidate": bool(r.get("paper_candidate")),
        }
        for r in top[:10]
    ]
    if audit["up_down_kalshi_rows"] and audit["up_down_polymarket_rows"] and audit["up_down_exact_window_matches"] == 0:
        audit["warning"] = "up_down_rows_exist_but_no_exact_window_matches"
    elif audit["up_down_exact_window_matches"] and audit["up_down_candidates_generated"] == 0:
        audit["warning"] = "up_down_exact_window_matches_exist_but_no_candidates_generated"


# ---------------------------------------------------------------------------- #
# Per-asset structural scan                                                    #
# ---------------------------------------------------------------------------- #


def _scan_asset(*, asset, kalshi, polymarket, cdna, opts: "_Opts", coverage: dict[str, Counter] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter]:
    all_rows = list(kalshi) + list(polymarket) + list(cdna)
    for r in all_rows:
        if not r.get("contract_family"):
            r["contract_family"] = _classify_row(r)
    rows_out: list[dict[str, Any]] = []
    grids_out: list[dict[str, Any]] = []
    mono_diag: Counter = Counter()
    if coverage is None:
        coverage = defaultdict(Counter)

    # Terminal-price lane: terminal_threshold + terminal_range share P_T, grouped
    # by instant. Directional/barrier never enter this state grid.
    terminal_rows = [
        r for r in all_rows
        if str(r.get("contract_family")) in TERMINAL_FAMILIES and r.get("target_instant_utc")
    ]
    by_instant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in terminal_rows:
        by_instant[r["target_instant_utc"]].append(r)

    for instant, group in sorted(by_instant.items()):
        grid = _build_state_grid(group)
        if len(grid) < 2:
            continue
        instruments = _base_instruments(group, grid, opts)
        synthetics = _synthetic_bucket_instruments(group, grid, opts)
        pool = instruments + synthetics
        grids_out.append(
            {
                "asset": asset, "target_instant_utc": instant, "states": len(grid),
                "state_grid": _grid_view(grid), "instruments": len(instruments),
                "synthetic_instruments": len(synthetics),
            }
        )
        rows_out.extend(_generate_candidates(asset=asset, instant=instant, grid=grid, pool=pool, group=group, opts=opts, mono_diag=mono_diag, coverage=coverage))

    # Directional-return lane (2-state, separate; never mixed with terminal price).
    rows_out.extend(_updown_candidates(asset=asset, rows=all_rows, opts=opts, coverage=coverage))
    # Barrier/touch lane (path-dependent; never matched to terminal or up/down).
    rows_out.extend(_barrier_rows(asset=asset, rows=all_rows, opts=opts, coverage=coverage))
    return rows_out, grids_out, mono_diag


def _barrier_rows(*, asset, rows, opts: "_Opts", coverage: dict[str, Counter] | None = None) -> list[dict[str, Any]]:
    """Barrier/touch contracts are path-dependent. They are surfaced as
    DIAGNOSTIC_ONLY and explicitly flagged as not comparable to terminal price or
    up/down contracts at the same instant."""
    cov = coverage if coverage is not None else defaultdict(Counter)
    out: list[dict[str, Any]] = []
    barrier = [r for r in rows if str(r.get("contract_family")) == CONTRACT_FAMILY_BARRIER_TOUCH]
    terminal_present = any(str(r.get("contract_family")) in TERMINAL_FAMILIES for r in rows)
    cov[CT_BARRIER]["attempted"] += len(barrier)
    for r in barrier:
        blockers = ["barrier_vs_terminal_mismatch"] if terminal_present else []
        out.append(
            {
                "lane": "barrier", "action": ACTION_WATCH, "paper_candidate": False,
                "paper_candidate_class": CLASS_NONE, "candidate_type": "BARRIER_TOUCH_DIAGNOSTIC",
                "contract_family": CONTRACT_FAMILY_BARRIER_TOUCH, "comparability_tier": "DIAGNOSTIC_ONLY",
                "asset": asset, "target_instant_utc": r.get("target_instant_utc"),
                "state_grid": [], "basket_legs": [], "payoff_vector": [], "min_payoff": None,
                "total_cost_after_fees": None, "net_edge_after_fees": None, "adjusted_net_edge_after_fees": None,
                "available_size_or_cap": None, "assumptions_accepted": [], "hard_blockers": blockers,
                "risk_notes": [
                    "barrier/touch is path-dependent (state = path max/min); not comparable to "
                    "terminal threshold/range or up/down. Internal basis/monotonicity only."
                ],
                "candidate_action": "", "strict_exact_arb": False,
            }
        )
    return out


# ---------------------------------------------------------------------------- #
# State grid + payoff vectors                                                  #
# ---------------------------------------------------------------------------- #


def _build_state_grid(rows: list[dict[str, Any]]) -> list[dict[str, float | None]]:
    boundaries: set[float] = set()
    for r in rows:
        for key in ("threshold_or_strike", "bucket_floor", "bucket_cap"):
            v = _to_float(r.get(key))
            if v is not None:
                boundaries.add(round(v, 4))
    ordered = sorted(boundaries)
    states: list[dict[str, float | None]] = []
    prev: float | None = None
    for b in ordered:
        states.append({"low": prev, "high": b})
        prev = b
    states.append({"low": prev, "high": None})
    return states


def _grid_view(grid: list[dict[str, float | None]]) -> list[str]:
    out: list[str] = []
    for s in grid:
        low = "-inf" if s["low"] is None else f"{s['low']:g}"
        high = "+inf" if s["high"] is None else f"{s['high']:g}"
        out.append(f"[{low},{high})")
    return out


def _vector_above(grid, strike: float) -> tuple[int, ...]:
    return tuple(1 if (s["low"] is not None and s["low"] >= strike - 1e-9) else 0 for s in grid)


def _vector_at_or_below(grid, strike: float) -> tuple[int, ...]:
    return tuple(1 if (s["high"] is not None and s["high"] <= strike + 1e-9) else 0 for s in grid)


def _vector_bucket(grid, floor: float | None, cap: float | None) -> tuple[int, ...]:
    out = []
    for s in grid:
        low_ok = floor is None or (s["low"] is not None and s["low"] >= floor - 1e-9)
        high_ok = cap is None or (s["high"] is not None and s["high"] <= cap + 1e-9)
        # Floor None == bottom tail (<=cap); cap None == top tail (>=floor).
        if floor is None:
            low_ok = True
        if cap is None:
            high_ok = True
        out.append(1 if (low_ok and high_ok) else 0)
    return tuple(out)


def _complement(vec: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(1 - v for v in vec)


# ---------------------------------------------------------------------------- #
# Instrument construction                                                       #
# ---------------------------------------------------------------------------- #


def _base_instruments(rows: list[dict[str, Any]], grid, opts: "_Opts") -> list[Instrument]:
    out: list[Instrument] = []
    for r in rows:
        platform = str(r.get("platform"))
        obs = str(r.get("payoff_observation_type"))
        comparator = str(r.get("comparator"))
        strike = _to_float(r.get("threshold_or_strike"))
        floor = _to_float(r.get("bucket_floor"))
        cap = _to_float(r.get("bucket_cap"))
        if obs == "range_at_target":
            yes_vec = _vector_bucket(grid, floor, cap)
        elif comparator == "above":
            yes_vec = _vector_above(grid, strike) if strike is not None else None
        elif comparator == "below":
            yes_vec = _vector_at_or_below(grid, strike) if strike is not None else None
        else:
            yes_vec = None
        if yes_vec is None:
            continue
        no_vec = _complement(yes_vec)
        q = r.get("quote") or {}
        # Complement-derive a missing executable ask from the opposite bid
        # (NO ask = 1 - YES bid) only when the operator accepts limited-depth
        # quotes (standard/aggressive). Conservative stays strict-ask-only.
        allow_comp = opts.risk_mode in {"standard", "aggressive"}
        for side, vec in (("YES", yes_vec), ("NO", no_vec)):
            ask, size, comp_used, comp_src = _side_ask_with_complement(q, side, allow_complement=allow_comp)
            leg = _make_leg(r, platform, side, ask, size, opts, complement_used=comp_used, complement_source=comp_src)
            out.append(Instrument(key=_leg_key(leg), vector=vec, legs=[leg], leg_vectors=[vec], label=f"{platform}:{side}:{leg.market_id_or_ticker}"))
    return out


def _make_leg(
    r: dict[str, Any], platform: str, side: str, ask: float | None, size: float | None, opts: "_Opts",
    *, complement_used: bool = False, complement_source: str | None = None,
) -> Leg:
    is_cdna = platform == "cdna"
    fee = _leg_fee(platform, ask)
    all_in = round(ask + fee, 8) if (ask is not None and fee is not None) else None
    blockers: list[str] = []
    risk_notes: list[str] = []
    if ask is None:
        blockers.append("missing_ask")
    if _stale(r, opts.generated, opts.max_quote_age_seconds):
        blockers.append("stale_or_missing_quote")
    # Available size: real CLOB size, else CDNA cap / top-of-book cap.
    if size is not None:
        avail = size
    elif is_cdna:
        avail = opts.cdna_operator_size_cap
        risk_notes.extend(["cdna_display_price_only", "cdna_executable_size_unverified"])
    elif opts.depth_permissive:
        avail = opts.operator_size_cap
    else:
        avail = None
        blockers.append("missing_quote_depth")
    if complement_used:
        # A complement-derived ask is a limited-depth quote: cap to the operator
        # size and flag it. Never treated as deep top-of-book.
        if opts.depth_permissive and opts.operator_size_cap > 0:
            avail = min(avail, opts.operator_size_cap) if avail is not None else opts.operator_size_cap
        risk_notes.append(
            f"{side} ask complement-derived ({complement_source}); limited depth, operator size cap applied."
        )
    display_side = {"YES": "DISPLAY_YES", "NO": "DISPLAY_NO"}.get(side, side) if is_cdna else side
    quote = r.get("quote") or {}
    token_ids = r.get("token_ids") or {}
    return Leg(
        platform=platform, asset=str(r.get("asset")), target_instant_utc=str(r.get("target_instant_utc")),
        market_shape=str(r.get("market_shape")), payoff_observation_type=str(r.get("payoff_observation_type")),
        side=display_side, ask=ask, fee=fee, all_in_cost=all_in, available_size_or_cap=avail,
        source_index=r.get("price_source"), market_id_or_ticker=r.get("market_id_or_ticker"),
        hard_blockers=blockers, risk_notes=risk_notes,
        complement_used=complement_used, complement_source=complement_source,
        condition_id=r.get("condition_id"), token_ids=dict(token_ids) if isinstance(token_ids, dict) else {},
        contract_id=r.get("contract_id"),
        quote_timestamp=quote.get("quote_timestamp"), depth_status=quote.get("depth_status"),
    )


def _synthetic_bucket_instruments(rows: list[dict[str, Any]], grid, opts: "_Opts") -> list[Instrument]:
    """YES-only Kalshi bucket baskets that synthesize cumulative thresholds.

    Only YES on mutually-exclusive constituent buckets is used (never NO on many
    buckets). One basket per boundary that a complement-venue threshold sits on,
    plus the exhaustive "all buckets" cover.
    """
    buckets = [
        r for r in rows
        if str(r.get("platform")) == "kalshi"
        and (r.get("bucket_floor") is not None or r.get("bucket_cap") is not None)
    ]
    if not buckets:
        return []
    out: list[Instrument] = []

    # Exhaustive cover: YES on every bucket -> pays 1 in every state (if exhaustive).
    exhaustive_vec = _sum_vectors([_vector_bucket(grid, _to_float(b.get("bucket_floor")), _to_float(b.get("bucket_cap"))) for b in buckets])
    if exhaustive_vec is not None and min(exhaustive_vec) >= 1 and len(buckets) <= opts.max_basket_legs:
        legs = [_bucket_leg(b, opts) for b in buckets]
        out.append(Instrument(key="synthetic_exhaustive_cover", vector=_clip(exhaustive_vec), legs=legs,
                              leg_vectors=[_vector_bucket(grid, _to_float(b.get("bucket_floor")), _to_float(b.get("bucket_cap"))) for b in buckets],
                              label="kalshi:SYNTHETIC_EXHAUSTIVE_COVER"))

    # Threshold-aligned syntheses for strikes present on a complement venue.
    complement_strikes = sorted({
        _round4(_to_float(r.get("threshold_or_strike")))
        for r in rows
        if str(r.get("platform")) in {"polymarket", "cdna"}
        and str(r.get("payoff_observation_type")) == "point_in_time_at_target"
        and r.get("threshold_or_strike") is not None
    })
    for x in complement_strikes:
        for kind, predicate in (("above", _vector_above(grid, x)), ("not_above", _vector_at_or_below(grid, x))):
            legs_src = _bucket_legs_for_predicate(buckets, x, kind)
            if not legs_src or len(legs_src) > opts.max_basket_legs:
                continue
            vec = _sum_vectors([_vector_bucket(grid, _to_float(b.get("bucket_floor")), _to_float(b.get("bucket_cap"))) for b in legs_src])
            if vec is None or tuple(_clip(vec)) != predicate:
                # Coverage does not exactly reproduce the threshold -> skip (incomplete).
                continue
            legs = [_bucket_leg(b, opts) for b in legs_src]
            out.append(Instrument(key=f"synthetic_{kind}_{x:g}", vector=predicate, legs=legs,
                                  leg_vectors=[_vector_bucket(grid, _to_float(b.get("bucket_floor")), _to_float(b.get("bucket_cap"))) for b in legs_src],
                                  label=f"kalshi:SYNTHETIC_{kind.upper()}_{x:g}"))
    return out


def _bucket_legs_for_predicate(buckets, strike: float, kind: str) -> list[dict[str, Any]]:
    out = []
    for b in buckets:
        floor = _to_float(b.get("bucket_floor"))
        cap = _to_float(b.get("bucket_cap"))
        if kind == "above":
            if floor is not None and floor >= strike - 1e-9:
                out.append(b)
        else:  # not_above
            if cap is not None and cap <= strike + 1e-9:
                out.append(b)
    return out


def _bucket_leg(b: dict[str, Any], opts: "_Opts") -> Leg:
    q = b.get("quote") or {}
    return _make_leg(b, "kalshi", "YES", _valid_ask(q.get("yes_ask")), _to_float(q.get("yes_ask_size")), opts)


# ---------------------------------------------------------------------------- #
# Candidate generators                                                          #
# ---------------------------------------------------------------------------- #


def _generate_candidates(*, asset, instant, grid, pool: list[Instrument], group, opts: "_Opts", mono_diag: Counter, coverage: dict[str, Counter] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grid_view = _grid_view(grid)
    cov = coverage if coverage is not None else defaultdict(Counter)

    # G1: single-instrument guaranteed payoff (e.g. exhaustive bucket cover < $1).
    for inst in pool:
        mn = min(inst.vector) if inst.vector else 0
        cost = inst.all_in_cost
        if mn >= 1:
            cov[CT_LONG_ONLY]["attempted"] += 1
            if cost is not None and cost < mn:
                rows.append(_candidate_row(asset, instant, grid_view, [inst], inst.vector, CT_LONG_ONLY, opts))

    # G2: guaranteed pairs (cover all states). Emit positive-net covers always;
    # blocked (missing-ask/stale), synthetic, AND a BOUNDED sample of net-negative
    # covers are emitted too, so cross-venue / long-only near-misses are visible —
    # not just monotonicity covers. Every guaranteed-cover attempt is counted in
    # ``coverage`` even when the row itself is sampled out.
    n = len(pool)
    # PER-CLASS sample budgets so a numerous class (long-only) can't crowd a
    # sparse one (cross-venue) out of the non-positive sample.
    emitted_blocked: Counter = Counter()
    emitted_synth: Counter = Counter()
    emitted_negative: Counter = Counter()
    for i in range(n):
        a = pool[i]
        for j in range(i + 1, n):
            b = pool[j]
            if len(a.legs) + len(b.legs) > opts.max_basket_legs:
                continue
            combined = tuple(x + y for x, y in zip(a.vector, b.vector))
            mn = min(combined)
            if mn < 1:
                continue
            ct = _classify_pair(a, b)
            cov[ct]["attempted"] += 1
            cost = a.all_in_cost
            cost2 = b.all_in_cost
            missing = cost is None or cost2 is None
            total = None if missing else round(cost + cost2, 8)
            positive = total is not None and total < mn
            is_synth = a.key.startswith("synthetic_") or b.key.startswith("synthetic_")
            emit = positive
            if not emit and missing:
                emit = emitted_blocked[ct] < _G2_SAMPLE_CAP
                if emit:
                    emitted_blocked[ct] += 1
            elif not emit and is_synth:
                emit = emitted_synth[ct] < _G2_SAMPLE_CAP
                if emit:
                    emitted_synth[ct] += 1
            elif not emit:  # net-negative non-synthetic: sample so near-misses show
                emit = emitted_negative[ct] < _G2_SAMPLE_CAP
                if emit:
                    emitted_negative[ct] += 1
            if emit:
                rows.append(_candidate_row(asset, instant, grid_view, [a, b], combined, ct, opts))

    # G3: same-payoff cheaper basket (identical vector, different cost) -> relative value.
    by_vec: dict[tuple[int, ...], list[Instrument]] = defaultdict(list)
    for inst in pool:
        if inst.all_in_cost is not None:
            by_vec[inst.vector].append(inst)
    for vec, insts in by_vec.items():
        if len(insts) < 2 or set(vec) == {0}:
            continue
        insts_sorted = sorted(insts, key=lambda x: x.all_in_cost)
        cheapest = insts_sorted[0]
        for other in insts_sorted[1:]:
            if other.all_in_cost - cheapest.all_in_cost > 1e-9 and cheapest.platforms != other.platforms:
                cov[CT_SAME_PAYOFF_CHEAPER]["attempted"] += 1
                rows.append(
                    _same_payoff_row(asset, instant, grid_view, cheapest, other, vec, opts)
                )

    # G4: monotonicity diagnostic per (platform, source, instant).
    g4 = _monotonicity_rows(asset, instant, grid_view, group, opts)
    cov[CT_MONOTONICITY]["attempted"] += len(g4)
    rows.extend(g4)
    # G5: threshold->bucket diagnostic (adjacent thresholds imply a range; needs shorting).
    g5 = _threshold_to_bucket_rows(asset, instant, grid_view, group, opts)
    cov[CT_THRESHOLD_TO_BUCKET]["attempted"] += len(g5)
    rows.extend(g5)
    # G6: threshold monotonicity covers — YES(>L) + NO(>U) for L<U, the buy-only
    # actionable expression of a monotonicity relationship/violation.
    pairs_before = int(mono_diag.get("pairs_checked", 0))
    g6 = _monotonicity_cover_rows(asset, instant, grid, grid_view, group, opts, mono_diag)
    cov[CT_MONOTONICITY_COVER]["attempted"] += int(mono_diag.get("pairs_checked", 0)) - pairs_before
    rows.extend(g6)
    return rows


def _monotonicity_cover_rows(asset, instant, grid, grid_view, group, opts: "_Opts", mono_diag: Counter) -> list[dict[str, Any]]:
    thr = [
        r for r in group
        if str(r.get("contract_family")) == CONTRACT_FAMILY_TERMINAL_THRESHOLD
        and str(r.get("comparator")) == "above"
        and r.get("threshold_or_strike") is not None
    ]
    thr.sort(key=lambda r: float(r["threshold_or_strike"]))
    rows: list[dict[str, Any]] = []
    emitted_blocked = 0
    for i in range(len(thr)):
        for j in range(i + 1, len(thr)):
            lo, hi = thr[i], thr[j]
            low_strike = float(lo["threshold_or_strike"])
            high_strike = float(hi["threshold_or_strike"])
            if not (low_strike < high_strike - 1e-9):
                continue  # same strike -> a cross-venue complement, not a ladder cover
            mono_diag["pairs_checked"] += 1
            row = _build_mono_cover(asset, instant, grid, grid_view, lo, hi, low_strike, high_strike, opts, mono_diag)
            positive = row["net_edge_after_fees"] is not None and row["net_edge_after_fees"] > 0
            # Positive-net covers always emit; blocked/negative covers are sampled
            # (capped) so they are visible without flooding a deep ladder.
            if positive:
                rows.append(row)
                mono_diag["generated"] += 1
                if row["paper_candidate"]:
                    mono_diag["paper_candidates"] += 1
            elif emitted_blocked < 30:
                rows.append(row)
                mono_diag["generated"] += 1
                emitted_blocked += 1
    return rows


def _no_ask_with_complement(row: dict[str, Any]) -> tuple[float | None, float | None, bool]:
    """Return ``(no_ask, no_size, complement_used)``. Prefer the direct NO ask;
    fall back to a complement-derived ask from an executable YES bid
    (NO ask = 1 - YES bid) — a limited-depth quote, flagged for the operator."""
    ask, size, used, _src = _side_ask_with_complement(row.get("quote") or {}, "NO")
    return ask, size, used


def _side_ask_with_complement(
    quote: dict[str, Any], side: str, *, allow_complement: bool = True
) -> tuple[float | None, float | None, bool, str | None]:
    """Return ``(ask, size, complement_used, complement_source)`` for ``side``.

    Prefer the direct executable ask. When it is missing, fall back to the binary
    complement of the *opposite executable bid* — ``NO ask = 1 - YES bid`` (or
    ``YES ask = 1 - NO bid``). This is venue-valid for a two-outcome binary market
    (YES + NO settle to $1) and is flagged for the operator as a limited-depth
    quote. Never a midpoint."""
    is_yes = str(side).upper().endswith("YES")
    direct_key = "yes_ask" if is_yes else "no_ask"
    direct_size_key = "yes_ask_size" if is_yes else "no_ask_size"
    direct = _valid_ask(quote.get(direct_key))
    if direct is not None:
        return direct, _to_float(quote.get(direct_size_key)), False, None
    if not allow_complement:
        return None, None, False, None
    opp_bid_key = "no_bid" if is_yes else "yes_bid"
    opp_bid_size_key = "no_bid_size" if is_yes else "yes_bid_size"
    opp_bid = _to_float(quote.get(opp_bid_key))
    if opp_bid is not None and 0.0 <= opp_bid <= 1.0:
        source = f"{'yes_ask' if is_yes else 'no_ask'} = 1 - {opp_bid_key}"
        return round(1.0 - opp_bid, 6), _to_float(quote.get(opp_bid_size_key)), True, source
    return None, None, False, None


# Venue + side-specific "which exact buy leg is missing" labels. These are
# DIAGNOSTIC ONLY (not policy hard-blockers): they live on the row's
# ``quote_side_diagnostics`` so the dashboard can name the precise unquoted leg
# without changing candidate gating.
def _venue_side_missing_label(leg: Leg) -> str:
    platform = str(leg.platform)
    is_yes = str(leg.side).upper().endswith("YES")
    if platform == "cdna":
        return "missing_cdna_display_yes" if is_yes else "missing_cdna_display_no"
    if platform == "kalshi":
        return "missing_kalshi_yes_ask" if is_yes else "missing_kalshi_no_ask"
    if platform == "polymarket":
        return "missing_polymarket_yes_ask" if is_yes else "missing_polymarket_no_ask"
    return f"missing_{platform}_{'yes' if is_yes else 'no'}_ask"


def _venue_stale_label(platform: str) -> str:
    p = str(platform)
    if p in {"kalshi", "polymarket", "cdna"}:
        return f"stale_{p}_quote"
    return f"stale_{p}_quote"


def _is_specific_missing_ask_label(label: Any) -> bool:
    text = str(label or "")
    return text.startswith("missing_") and (
        text.endswith("_ask") or text in {"missing_cdna_display_yes", "missing_cdna_display_no"}
    ) and text != "missing_ask"


def _leg_quote_side_diagnostics(legs: list[Leg], opts: "_Opts") -> list[str]:
    """Precise venue+side missing-ask / stale / complement diagnostics for a row."""
    out: list[str] = []
    for leg in legs:
        if leg.ask is None:
            out.append(_venue_side_missing_label(leg))
        if "stale_or_missing_quote" in leg.hard_blockers:
            out.append(_venue_stale_label(leg.platform))
        if leg.complement_used:
            label = "complement_quote_used"
            if leg.complement_source:
                label = f"complement_quote_used:{leg.complement_source}"
            out.append(label)
    # Stable, de-duplicated order for readable dashboards.
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _build_mono_cover(asset, instant, grid, grid_view, lo, hi, low_strike, high_strike, opts: "_Opts", mono_diag: Counter) -> dict[str, Any]:
    q_lo = lo.get("quote") or {}
    yes_lower_ask = _valid_ask(q_lo.get("yes_ask"))
    yes_lower_size = _to_float(q_lo.get("yes_ask_size"))
    no_higher_ask, no_higher_size, complement_used, complement_source = _side_ask_with_complement(hi.get("quote") or {}, "NO")
    if complement_used:
        mono_diag["complement_quote_used"] += 1

    leg_yes = _make_leg(lo, str(lo["platform"]), "YES", yes_lower_ask, yes_lower_size, opts)
    leg_no = _make_leg(
        hi, str(hi["platform"]), "NO", no_higher_ask, no_higher_size, opts,
        complement_used=complement_used, complement_source=complement_source,
    )
    legs = [leg_yes, leg_no]

    blockers: list[str] = []
    if yes_lower_ask is None:
        blockers.append("missing_yes_lower_ask")
        mono_diag["missing_yes_lower_ask"] += 1
    if no_higher_ask is None:
        blockers.append("missing_no_higher_ask")
        mono_diag["missing_no_higher_ask"] += 1
    if not (low_strike < high_strike):
        blockers.append("threshold_order_invalid")
    if _stale(lo, opts.generated, opts.max_quote_age_seconds) or _stale(hi, opts.generated, opts.max_quote_age_seconds):
        blockers.append("stale_or_missing_quote")
    if leg_yes.available_size_or_cap is None or leg_no.available_size_or_cap is None:
        blockers.append("missing_quote_depth")

    cross_source = bool(lo.get("price_source") and hi.get("price_source") and lo.get("price_source") != hi.get("price_source"))
    cross_platform = lo.get("platform") != hi.get("platform")
    cross = cross_source or cross_platform
    if cross:
        blockers.append("source_index_mismatch")

    total_cost = None
    net = None
    if leg_yes.all_in_cost is not None and leg_no.all_in_cost is not None:
        total_cost = round(leg_yes.all_in_cost + leg_no.all_in_cost, 8)
        net = round(1.0 - total_cost, 8)
        if net <= 0:
            blockers.append("no_positive_net_edge_after_fees")
    basis_buffer = opts.basis_buffer_edge if cross else 0.0
    adjusted = round(net - basis_buffer, 8) if net is not None else None
    if net is not None and net > 0 and adjusted is not None and adjusted <= 0:
        blockers.append("no_positive_adjusted_net_edge_after_basis_buffer")

    accepted_basis = opts.risk_mode in {"standard", "aggressive"}
    hard = collect_hard_blockers(blockers, accepted_basis=accepted_basis, accepted_top_of_book_size_cap=opts.depth_permissive)
    paper = bool(
        opts.risk_mode == "aggressive"
        and net is not None and net > 0 and adjusted is not None and adjusted > 0 and not hard
    )

    payoff_vec = tuple(a + b for a, b in zip(_vector_above(grid, low_strike), _vector_at_or_below(grid, high_strike)))
    min_payoff = float(min(payoff_vec)) if payoff_vec else 1.0
    max_payoff = float(max(payoff_vec)) if payoff_vec else 1.0
    depth_used = opts.depth_permissive and "missing_quote_depth" in blockers

    assumptions: list[str] = []
    risk_notes: list[str] = []
    paper_class = CLASS_NONE
    candidate_action = ""
    action = ACTION_IGNORE if hard else ACTION_WATCH
    if paper:
        paper_class = CLASS_OPERATOR if cross else CLASS_STRICT
        candidate_action = "PAPER_TEST_OR_MANUAL_MICRO_TEST"
        action = ACTION_PAPER
        if cross:
            assumptions.append("source_index_basis_risk_accepted")
        if depth_used:
            assumptions.append("limited_depth_operator_size_cap_applied")
        if complement_used:
            assumptions += ["complement_quote_used", "limited_depth_operator_size_cap_applied"]
            risk_notes.append("NO(higher) ask was complement-derived from an executable YES bid; limited depth.")
    assumptions = sorted(set(assumptions))
    tier = "EXACT_SAME_PAYOFF" if (paper and paper_class == CLASS_STRICT) else ("OPERATOR_RELATIVE_VALUE" if cross else "DIAGNOSTIC_ONLY")

    return {
        "action": action,
        "paper_candidate": paper,
        "paper_candidate_class": paper_class,
        "candidate_type": CT_MONOTONICITY_COVER,
        "contract_family": CONTRACT_FAMILY_TERMINAL_THRESHOLD,
        "comparability_tier": tier,
        "asset": asset,
        "target_instant_utc": instant,
        "lower_strike": low_strike,
        "higher_strike": high_strike,
        "yes_lower_ask": yes_lower_ask,
        "no_higher_ask": no_higher_ask,
        "complement_quote_used": complement_used,
        "quote_side_diagnostics": _leg_quote_side_diagnostics(legs, opts),
        "state_grid": grid_view,
        "basket_legs": [leg_yes.to_dict(list(_vector_above(grid, low_strike))), leg_no.to_dict(list(_vector_at_or_below(grid, high_strike)))],
        "payoff_vector": list(payoff_vec),
        "min_payoff": min_payoff,
        "max_payoff": max_payoff,
        "total_cost_after_fees": total_cost,
        "net_edge_after_fees": net,
        "source_basis_buffer": basis_buffer,
        "adjusted_net_edge_after_fees": adjusted,
        "available_size_or_cap": _basket_available(legs, opts, paper),
        "assumptions_accepted": assumptions,
        "hard_blockers": hard,
        "risk_notes": risk_notes,
        "candidate_action": candidate_action,
        "strict_exact_arb": bool(paper and paper_class == CLASS_STRICT),
    }


def _classify_pair(a: Instrument, b: Instrument) -> str:
    synthetic = a.key.startswith("synthetic_") or b.key.startswith("synthetic_")
    if synthetic:
        return CT_BUCKET_TO_THRESHOLD
    base_threshold = all(len(x.legs) == 1 and x.legs[0].payoff_observation_type == "point_in_time_at_target" for x in (a, b))
    if base_threshold and (a.platforms | b.platforms) and a.platforms != b.platforms:
        return CT_CROSS_VENUE
    return CT_LONG_ONLY


def _missing_ask_blocker(leg: Leg, is_synthetic: bool) -> str:
    if leg.platform == "cdna":
        return _venue_side_missing_label(leg)
    if is_synthetic:
        return "missing_bucket_leg_ask"
    return _venue_side_missing_label(leg)


def _leg_hard_blockers_for_row(leg: Leg, is_synthetic: bool) -> list[str]:
    out: list[str] = []
    for blocker in leg.hard_blockers:
        if blocker == "missing_ask":
            out.append(_missing_ask_blocker(leg, is_synthetic))
        else:
            out.append(blocker)
    return out


def _candidate_row(asset, instant, grid_view, instruments: list[Instrument], vector, candidate_type, opts: "_Opts") -> dict[str, Any]:
    legs: list[Leg] = [leg for inst in instruments for leg in inst.legs]
    leg_vecs = [lv for inst in instruments for lv in inst.leg_vectors]
    min_payoff = float(min(vector)) if vector else 0.0
    total_cost = round(sum(leg.all_in_cost for leg in legs), 8) if all(leg.all_in_cost is not None for leg in legs) else None
    net = round(min_payoff - total_cost, 8) if total_cost is not None else None

    blockers: list[str] = []
    for inst in instruments:
        is_synth = inst.key.startswith("synthetic_")
        for leg in inst.legs:
            blockers.extend(_leg_hard_blockers_for_row(leg, is_synth))
    if total_cost is None or net is None:
        if not any(_is_specific_missing_ask_label(b) for b in blockers):
            blockers.append("missing_ask")
    elif net <= 0:
        blockers.append("no_positive_net_edge_after_fees")
    if len(legs) > opts.max_basket_legs:
        blockers.append("synthetic_bucket_coverage_incomplete")

    is_cdna = any(leg.platform == "cdna" for leg in legs)
    sources = {str(leg.source_index) for leg in legs if leg.source_index}
    cross_source = len(sources) > 1
    if cross_source:
        blockers.append("source_index_mismatch")

    # Source-basis buffer: haircut the edge by the operator's assumed feed
    # difference (CF Benchmarks vs Binance vs CDNA) for cross-source candidates.
    basis_buffer = opts.basis_buffer_edge if cross_source else 0.0
    adjusted = round(net - basis_buffer, 8) if net is not None else None
    if net is not None and net > 0 and adjusted is not None and adjusted <= 0:
        blockers.append("no_positive_adjusted_net_edge_after_basis_buffer")

    accepted_basis = opts.risk_mode in {"standard", "aggressive"}
    accepted_cdna = is_cdna and opts.operator_accept_cdna and opts.risk_mode in {"standard", "aggressive"}
    hard = collect_hard_blockers(
        blockers, ignore_cdna_info=accepted_cdna, accepted_basis=accepted_basis,
        accepted_top_of_book_size_cap=opts.depth_permissive,
    )

    depth_used = opts.depth_permissive and "missing_quote_depth" in blockers
    complement_used = any(leg.complement_used for leg in legs)
    assumptions: list[str] = []
    if accepted_basis and cross_source:
        assumptions.append("source_index_mismatch")
    if depth_used:
        assumptions.append("limited_depth_operator_size_cap_applied")
    if complement_used:
        assumptions += ["complement_quote_used", "limited_depth_operator_size_cap_applied"]
    if accepted_cdna:
        assumptions += ["cdna_display_price_only", "cdna_executable_size_unverified"]
    assumptions = sorted(set(assumptions))

    paper = bool(net is not None and net > 0 and adjusted is not None and adjusted > 0 and not hard)
    if paper and is_cdna and not accepted_cdna:
        paper = False
    if paper and cross_source and not accepted_basis:
        paper = False

    paper_class = CLASS_NONE
    action = ACTION_IGNORE if hard else ACTION_WATCH
    candidate_action = ""
    risk_notes: list[str] = []
    if paper:
        if is_cdna:
            paper_class = CLASS_CDNA
            candidate_action = "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
            risk_notes.append("CDNA leg is display-price/fill-first; fill CDNA first, then hedge the exact filled quantity.")
        elif cross_source or depth_used or complement_used:
            paper_class = CLASS_OPERATOR
            candidate_action = "PAPER_TEST_OR_MANUAL_MICRO_TEST"
        else:
            paper_class = CLASS_STRICT
            candidate_action = "PAPER_TEST_OR_MANUAL_MICRO_TEST"
        action = ACTION_PAPER
    if complement_used:
        risk_notes.append("One or more legs used a complement-derived ask (1 - opposite bid); limited depth.")

    tier = _comparability_tier(
        candidate_type, is_cdna=is_cdna, cross_source=cross_source, depth_used=(depth_used or complement_used),
    )
    available = _basket_available(legs, opts, paper)
    quote_side_diagnostics = _leg_quote_side_diagnostics(legs, opts)
    return {
        "action": action,
        "paper_candidate": paper,
        "paper_candidate_class": paper_class,
        "candidate_type": candidate_type,
        "contract_family": _basket_family(legs),
        "comparability_tier": tier,
        "asset": asset,
        "target_instant_utc": instant,
        "state_grid": grid_view,
        "basket_legs": [leg.to_dict(leg_vecs[i] if i < len(leg_vecs) else None) for i, leg in enumerate(legs)],
        "payoff_vector": list(vector),
        "min_payoff": min_payoff,
        "total_cost_after_fees": total_cost,
        "net_edge_after_fees": net,
        "source_basis_buffer": basis_buffer,
        "adjusted_net_edge_after_fees": adjusted,
        "available_size_or_cap": available,
        "assumptions_accepted": assumptions,
        "hard_blockers": hard,
        "quote_side_diagnostics": quote_side_diagnostics,
        "complement_quote_used": complement_used,
        "risk_notes": risk_notes,
        "candidate_action": candidate_action,
        "strict_exact_arb": bool(paper and paper_class == CLASS_STRICT),
    }


def _comparability_tier(candidate_type: str, *, is_cdna: bool, cross_source: bool, depth_used: bool) -> str:
    if candidate_type in (CT_MONOTONICITY, CT_THRESHOLD_TO_BUCKET):
        return "DIAGNOSTIC_ONLY"
    if candidate_type == CT_BUCKET_TO_THRESHOLD:
        return "SYNTHETIC_SAME_PAYOFF"
    if is_cdna or cross_source or depth_used:
        return "OPERATOR_RELATIVE_VALUE"
    return "EXACT_SAME_PAYOFF"


def _basket_family(legs: list[Leg]) -> str:
    fams = set()
    for leg in legs:
        fams.add(CONTRACT_FAMILY_TERMINAL_RANGE if leg.payoff_observation_type == "range_at_target" else CONTRACT_FAMILY_TERMINAL_THRESHOLD)
    if fams == {CONTRACT_FAMILY_TERMINAL_RANGE}:
        return CONTRACT_FAMILY_TERMINAL_RANGE
    if fams == {CONTRACT_FAMILY_TERMINAL_THRESHOLD}:
        return CONTRACT_FAMILY_TERMINAL_THRESHOLD
    return "terminal_threshold_and_range"


def _same_payoff_row(asset, instant, grid_view, cheaper: Instrument, dearer: Instrument, vector, opts: "_Opts") -> dict[str, Any]:
    legs = list(cheaper.legs)
    leg_vecs = list(cheaper.leg_vectors)
    min_payoff = float(min(vector))
    total_cost = cheaper.all_in_cost
    # Same-payoff is relative value: a paper candidate only when the cheaper basket
    # itself is a guaranteed >=$1 cover bought for < its min payoff.
    guaranteed = min_payoff >= 1 and total_cost is not None and total_cost < min_payoff
    net = round(min_payoff - total_cost, 8) if (guaranteed and total_cost is not None) else None
    blockers = list(cheaper.leg_blockers())
    if not guaranteed:
        blockers.append("requires_short_or_not_guaranteed")
    hard = collect_hard_blockers(blockers, accepted_basis=opts.risk_mode in {"standard", "aggressive"}, accepted_top_of_book_size_cap=opts.depth_permissive)
    paper = bool(guaranteed and net is not None and net > 0 and not hard)
    return {
        "action": ACTION_PAPER if paper else ACTION_WATCH,
        "paper_candidate": paper,
        "paper_candidate_class": (CLASS_CDNA if cheaper.is_cdna else CLASS_OPERATOR) if paper else CLASS_NONE,
        "candidate_type": CT_SAME_PAYOFF_CHEAPER,
        "contract_family": _basket_family(legs),
        "comparability_tier": "OPERATOR_RELATIVE_VALUE",
        "asset": asset,
        "target_instant_utc": instant,
        "state_grid": grid_view,
        "basket_legs": [leg.to_dict(leg_vecs[i] if i < len(leg_vecs) else None) for i, leg in enumerate(legs)],
        "payoff_vector": list(vector),
        "min_payoff": min_payoff,
        "total_cost_after_fees": total_cost,
        "net_edge_after_fees": net,
        "source_basis_buffer": 0.0,
        "adjusted_net_edge_after_fees": net,
        "available_size_or_cap": _basket_available(legs, opts, paper),
        "assumptions_accepted": [],
        "hard_blockers": hard,
        "risk_notes": [
            f"cheaper={cheaper.label} cost={total_cost}; dearer={dearer.label} cost={dearer.all_in_cost}",
        ],
        "candidate_action": "PAPER_TEST_OR_MANUAL_MICRO_TEST" if paper else "",
        "strict_exact_arb": False,
    }


def _monotonicity_rows(asset, instant, grid_view, group, opts: "_Opts") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_src: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in group:
        if str(r.get("payoff_observation_type")) != "point_in_time_at_target" or str(r.get("comparator")) != "above":
            continue
        if r.get("threshold_or_strike") is None:
            continue
        by_src[(str(r.get("platform")), str(r.get("price_source")))].append(r)
    for (platform, source), markets in by_src.items():
        ordered = sorted(markets, key=lambda r: float(r["threshold_or_strike"]))
        for lo, hi in zip(ordered, ordered[1:]):
            lo_ask = _valid_ask((lo.get("quote") or {}).get("yes_ask"))
            hi_ask = _valid_ask((hi.get("quote") or {}).get("yes_ask"))
            if lo_ask is None or hi_ask is None:
                continue
            # P(>lower) should be >= P(>higher): the lower-strike YES should not be cheaper.
            if lo_ask + _leg_fee(platform, lo_ask) < hi_ask - _leg_fee(platform, hi_ask) - 1e-9:
                rows.append(
                    {
                        "action": ACTION_WATCH, "paper_candidate": False, "paper_candidate_class": CLASS_NONE,
                        "candidate_type": CT_MONOTONICITY, "asset": asset, "target_instant_utc": instant,
                        "contract_family": CONTRACT_FAMILY_TERMINAL_THRESHOLD, "comparability_tier": "DIAGNOSTIC_ONLY",
                        "state_grid": grid_view, "basket_legs": [], "payoff_vector": [],
                        "min_payoff": None, "total_cost_after_fees": None, "net_edge_after_fees": None,
                        "available_size_or_cap": None, "assumptions_accepted": [], "hard_blockers": [],
                        "risk_notes": [
                            f"{platform} {source}: ask(above {lo['threshold_or_strike']})={lo_ask} < "
                            f"ask(above {hi['threshold_or_strike']})={hi_ask}; P(>lower) should be >= P(>higher). "
                            "Actionable buy-only via YES(lower)+NO(higher) appears in cross-strike pairs if it nets positive."
                        ],
                        "candidate_action": "",
                        "strict_exact_arb": False,
                    }
                )
    return rows


def _threshold_to_bucket_rows(asset, instant, grid_view, group, opts: "_Opts") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    thresholds = sorted(
        {float(r["threshold_or_strike"]) for r in group
         if str(r.get("payoff_observation_type")) == "point_in_time_at_target"
         and str(r.get("comparator")) == "above" and r.get("threshold_or_strike") is not None}
    )
    for lo, hi in zip(thresholds, thresholds[1:]):
        rows.append(
            {
                "action": ACTION_WATCH, "paper_candidate": False, "paper_candidate_class": CLASS_NONE,
                "candidate_type": CT_THRESHOLD_TO_BUCKET, "asset": asset, "target_instant_utc": instant,
                "contract_family": CONTRACT_FAMILY_TERMINAL_THRESHOLD, "comparability_tier": "DIAGNOSTIC_ONLY",
                "state_grid": grid_view, "basket_legs": [], "payoff_vector": [],
                "min_payoff": None, "total_cost_after_fees": None, "net_edge_after_fees": None,
                "available_size_or_cap": None, "assumptions_accepted": [],
                "hard_blockers": ["requires_short_or_not_guaranteed"],
                "risk_notes": [
                    f"P(>{lo:g}) - P(>{hi:g}) ~= P({lo:g} < X <= {hi:g}); trading exactly needs a short leg -> DIAGNOSTIC_ONLY."
                ],
                "candidate_action": "",
                "strict_exact_arb": False,
            }
        )
    return rows


def _updown_candidates(*, asset, rows, opts: "_Opts", coverage: dict[str, Counter] | None = None) -> list[dict[str, Any]]:
    cov = coverage if coverage is not None else defaultdict(Counter)
    ud = [r for r in rows if str(r.get("payoff_observation_type")) == "interval_start_to_end_change" and r.get("target_instant_utc")]
    out: list[dict[str, Any]] = []
    allow_comp = opts.risk_mode in {"standard", "aggressive"}
    for i in range(len(ud)):
        for j in range(i + 1, len(ud)):
            a, b = ud[i], ud[j]
            if a.get("platform") == b.get("platform"):
                continue
            if a.get("target_instant_utc") != b.get("target_instant_utc"):
                continue
            if not a.get("reference_start_utc") or a.get("reference_start_utc") != b.get("reference_start_utc"):
                continue
            if a.get("interval_length_seconds") != b.get("interval_length_seconds"):
                continue
            cov[CT_UP_DOWN]["attempted"] += 1
            # 2-state grid (down, up). a "up" YES + b "down" (=b up NO) covers both.
            # NO ask falls back to the binary complement of an executable YES bid.
            qa = a.get("quote") or {}
            qb = b.get("quote") or {}
            a_up, a_up_size, a_comp, a_src = _side_ask_with_complement(qa, "YES", allow_complement=allow_comp)
            b_down, b_down_size, b_comp, b_src = _side_ask_with_complement(qb, "NO", allow_complement=allow_comp)
            leg_a = _make_leg(a, str(a["platform"]), "YES", a_up, a_up_size, opts, complement_used=a_comp, complement_source=a_src)
            leg_b = _make_leg(b, str(b["platform"]), "NO", b_down, b_down_size, opts, complement_used=b_comp, complement_source=b_src)
            legs = [leg_a, leg_b]
            blockers = _leg_hard_blockers_for_row(leg_a, False) + _leg_hard_blockers_for_row(leg_b, False)
            total = None
            net = None
            cross_source = bool(a.get("price_source") and b.get("price_source") and a.get("price_source") != b.get("price_source"))
            if leg_a.all_in_cost is not None and leg_b.all_in_cost is not None:
                total = round(leg_a.all_in_cost + leg_b.all_in_cost, 8)
                net = round(1.0 - total, 8)
                if net <= 0:
                    blockers.append("no_positive_net_edge_after_fees")
            else:
                if not any(_is_specific_missing_ask_label(b) for b in blockers):
                    blockers.append("missing_ask")
            if cross_source:
                blockers.append("source_index_mismatch")
            basis_buffer = opts.basis_buffer_edge if cross_source else 0.0
            adjusted = round(net - basis_buffer, 8) if net is not None else None
            if net is not None and net > 0 and adjusted is not None and adjusted <= 0:
                blockers.append("no_positive_adjusted_net_edge_after_basis_buffer")
            complement_used = bool(a_comp or b_comp)
            hard = collect_hard_blockers(blockers, accepted_basis=opts.risk_mode in {"standard", "aggressive"}, accepted_top_of_book_size_cap=opts.depth_permissive)
            paper = bool(net is not None and net > 0 and adjusted is not None and adjusted > 0 and not hard)
            assumptions = ["source_index_mismatch"] if (paper and "source_index_mismatch" in blockers) else []
            if complement_used:
                assumptions += ["complement_quote_used", "limited_depth_operator_size_cap_applied"]
            out.append(
                {
                    "action": ACTION_PAPER if paper else (ACTION_IGNORE if hard else ACTION_WATCH),
                    "paper_candidate": paper,
                    "paper_candidate_class": CLASS_OPERATOR if paper else CLASS_NONE,
                    "candidate_type": CT_UP_DOWN, "asset": asset,
                    "contract_family": CONTRACT_FAMILY_DIRECTIONAL_RETURN,
                    "comparability_tier": "OPERATOR_RELATIVE_VALUE" if (cross_source or complement_used) else "EXACT_SAME_PAYOFF",
                    "target_instant_utc": a.get("target_instant_utc"),
                    "reference_start_utc": a.get("reference_start_utc"),
                    "state_grid": ["down", "up"], "basket_legs": [leg_a.to_dict([0, 1]), leg_b.to_dict([1, 0])],
                    "payoff_vector": [1, 1], "min_payoff": 1.0, "total_cost_after_fees": total,
                    "net_edge_after_fees": net, "source_basis_buffer": basis_buffer,
                    "adjusted_net_edge_after_fees": adjusted,
                    "available_size_or_cap": _basket_available(legs, opts, paper),
                    "assumptions_accepted": sorted(set(assumptions)), "hard_blockers": hard, "risk_notes": [],
                    "quote_side_diagnostics": _leg_quote_side_diagnostics(legs, opts),
                    "complement_quote_used": complement_used,
                    "candidate_action": "PAPER_TEST_OR_MANUAL_MICRO_TEST" if paper else "",
                    "strict_exact_arb": False,
                }
            )
    return out


# ---------------------------------------------------------------------------- #
# Helpers                                                                       #
# ---------------------------------------------------------------------------- #


def _basket_available(legs: list[Leg], opts: "_Opts", paper: bool) -> float | None:
    sizes = [leg.available_size_or_cap for leg in legs if leg.available_size_or_cap is not None]
    if len(sizes) != len(legs) or not sizes:
        return None
    avail = min(sizes)
    if paper and opts.operator_size_cap and avail > opts.operator_size_cap:
        avail = opts.operator_size_cap
    return avail


def _sum_vectors(vectors: list[tuple[int, ...]]) -> list[int] | None:
    if not vectors:
        return None
    length = len(vectors[0])
    out = [0] * length
    for v in vectors:
        if len(v) != length:
            return None
        for i, x in enumerate(v):
            out[i] += x
    return out


def _clip(vec: list[int]) -> tuple[int, ...]:
    return tuple(1 if x >= 1 else 0 for x in vec)


def _leg_key(leg: Leg) -> str:
    return f"{leg.platform}:{leg.market_id_or_ticker}:{leg.side}"


def _dedup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        leg_ids = tuple(sorted(f"{l.get('platform')}:{l.get('market_id_or_ticker')}:{l.get('side')}" for l in (r.get("basket_legs") or [])))
        key = (r.get("candidate_type"), r.get("asset"), r.get("target_instant_utc"), leg_ids, tuple(r.get("risk_notes") or []))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _leg_fee(platform: str, ask: float | None) -> float | None:
    if ask is None:
        return None
    if platform == "kalshi":
        return round(_KALSHI_FEE.fee_for_leg(ask), 6)
    if platform == "polymarket":
        return round(_POLY_FEE.fee_for_leg_for_category(ask, category="crypto"), 6)
    if platform == "cdna":
        return CDNA_FEE_PER_CONTRACT
    return None


def _valid_ask(value: Any) -> float | None:
    ask = _to_float(value)
    if ask is None or not 0.0 <= ask <= 1.0:
        return None
    return ask


def _valid_bid(value: Any) -> float | None:
    bid = _to_float(value)
    if bid is None or not 0.0 <= bid <= 1.0:
        return None
    return bid


def _stale(row: dict[str, Any], generated: datetime, max_age: float) -> bool:
    ts = (row.get("quote") or {}).get("quote_timestamp")
    parsed = _parse_dt(ts)
    if parsed is None:
        return True
    return (generated - parsed).total_seconds() > max_age


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round4(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1e9


def _classify_execution(row: dict[str, Any]) -> tuple[str, bool, bool]:
    """Return ``(candidate_execution_type, tradable_buy_only, requires_short_or_sell)``.

    Mason cannot short, so anything needing a sell/short leg is non-tradable and
    diagnostic-only; only BUY_ONLY rows are eligible to be paper candidates.
    """
    blockers = set(row.get("hard_blockers") or [])
    ct = row.get("candidate_type")
    if blockers & _REQUIRES_SHORT_BLOCKERS or ct == CT_THRESHOLD_TO_BUCKET:
        return "REQUIRES_SHORT", False, True
    if ct == CT_MONOTONICITY or row.get("lane") == "barrier":
        return "DIAGNOSTIC_ONLY", False, False
    return "BUY_ONLY", True, False


# Quote gaps that a fresh/complete book could fix -> a row is a "near-miss".
_MISSING_ASK_LIKE = {
    "missing_ask", "missing_yes_lower_ask", "missing_no_higher_ask",
    "missing_partner_yes_ask", "missing_partner_no_ask", "missing_bucket_leg_ask",
    "missing_cdna_display_price", "missing_quote_depth", "stale_or_missing_quote",
    "missing_lower_yes_ask", "missing_higher_no_ask", "missing_partner_complement_ask",
    "missing_kalshi_yes_ask", "missing_kalshi_no_ask",
    "missing_polymarket_yes_ask", "missing_polymarket_no_ask",
    "missing_cdna_display_yes", "missing_cdna_display_no",
}
# A buy-only row this close to positive after fees is worth watching for a quote tick.
_NEAR_MISS_NET_TOL = 0.02

_MISSING_HUMAN = {
    "missing_no_higher_ask": "NO ask on higher threshold",
    "missing_yes_lower_ask": "YES ask on lower threshold",
    "missing_higher_no_ask": "NO ask on higher threshold",
    "missing_lower_yes_ask": "YES ask on lower threshold",
    "missing_partner_no_ask": "NO ask on partner venue",
    "missing_partner_yes_ask": "YES ask on partner venue",
    "missing_partner_complement_ask": "partner complement bid for missing ask",
    "missing_kalshi_yes_ask": "Kalshi YES ask",
    "missing_kalshi_no_ask": "Kalshi NO ask",
    "missing_polymarket_yes_ask": "Polymarket YES ask",
    "missing_polymarket_no_ask": "Polymarket NO ask",
    "missing_cdna_display_yes": "CDNA display YES",
    "missing_cdna_display_no": "CDNA display NO",
    "missing_bucket_leg_ask": "YES ask on a bucket leg",
    "missing_cdna_display_price": "CDNA display price",
    "missing_quote_depth": "executable quote depth",
    "stale_or_missing_quote": "a fresh (non-stale) quote",
    "missing_ask": "a buy-leg ask",
}


def _human_missing(blocker: str) -> str:
    return _MISSING_HUMAN.get(blocker, blocker)


def _annotate_row_operability(row: dict[str, Any]) -> dict[str, Any]:
    """Tag buy-only rows with near-miss / manual-micro-test operator fields.

    ``manual_micro_test_candidate`` is only ever true for a fully-valid buy-only
    paper candidate (quotes exist, post-fee edge positive, no unaccepted hard
    blockers). ``near_miss`` flags a buy-only row that is one fixable quote gap —
    or a couple of cents of fee drag — away from qualifying."""
    buy_only = bool(row.get("tradable_buy_only"))
    paper = bool(row.get("paper_candidate"))
    blockers = list(row.get("hard_blockers") or [])
    net = row.get("net_edge_after_fees")
    row["manual_micro_test_candidate"] = bool(paper and buy_only and row.get("action") == ACTION_PAPER)

    near = False
    reason = ""
    missing_to: list[str] = []
    if buy_only and not paper:
        missing = [b for b in blockers if b in _MISSING_ASK_LIKE]
        if missing:
            near = True
            human = sorted({_human_missing(b) for b in missing})
            reason = "needs " + ", ".join(human)
            missing_to = human
        elif isinstance(net, (int, float)) and -_NEAR_MISS_NET_TOL <= float(net) <= 0:
            near = True
            reason = f"net edge {float(net):+.4f} after fees (within {_NEAR_MISS_NET_TOL:.3f} of positive)"
            missing_to = [f"positive net edge (currently {float(net):+.4f})"]
        elif "no_positive_adjusted_net_edge_after_basis_buffer" in blockers:
            near = True
            reason = "positive net before the source-basis buffer; buffer removed the edge"
            missing_to = ["a smaller basis buffer or tighter cross-venue quotes"]
    row["near_miss"] = near
    row["near_miss_reason"] = reason
    row["missing_to_candidate"] = missing_to
    return row


def _finalize_coverage(coverage: dict[str, Counter], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-candidate-class funnel: attempted (structural opportunities evaluated),
    generated (rows emitted), priced (computable net edge), paper (qualified).

    The attempted>generated gap reveals classes that ARE evaluated but sampled out
    or dropped (e.g. net-negative cross-venue covers), so "only monotonicity
    covers show up" can be distinguished from "nothing else was even attempted"."""
    generated: Counter = Counter()
    priced: Counter = Counter()
    net_positive: Counter = Counter()
    paper: Counter = Counter()
    blocked_missing_ask: Counter = Counter()
    blocked_stale: Counter = Counter()
    blocked_no_positive_net: Counter = Counter()
    blocked_shape_or_time: Counter = Counter()
    for r in rows:
        ct = _coverage_class_for_row(r)
        if not ct:
            continue
        generated[ct] += 1
        if r.get("net_edge_after_fees") is not None:
            priced[ct] += 1
            if _safe_float(r.get("net_edge_after_fees")) > 0:
                net_positive[ct] += 1
        if r.get("paper_candidate"):
            paper[ct] += 1
        blockers = list(r.get("hard_blockers") or []) + list(r.get("quote_side_diagnostics") or [])
        if any(_coverage_missing_ask_blocker(b) for b in blockers):
            blocked_missing_ask[ct] += 1
        if any("stale" in str(b) for b in blockers):
            blocked_stale[ct] += 1
        if "no_positive_net_edge_after_fees" in blockers:
            blocked_no_positive_net[ct] += 1
        if any(str(b) in {"target_time_mismatch", "target_date_mismatch", "timezone_mismatch", "incompatible_shape", "reference_start_mismatch", "interval_length_mismatch", "barrier_vs_terminal_mismatch"} for b in blockers):
            blocked_shape_or_time[ct] += 1
    classes: list[str] = list(CANDIDATE_CLASSES)
    coverage_keys = [_coverage_class_for_candidate_type(ct) for ct in coverage.keys()]
    for ct in list(generated.keys()) + coverage_keys:
        if ct not in classes:
            classes.append(str(ct))
    out: list[dict[str, Any]] = []
    for ct in classes:
        attempted = sum(
            int(counter.get("attempted", 0))
            for raw_ct, counter in coverage.items()
            if _coverage_class_for_candidate_type(raw_ct) == ct
        )
        gen = int(generated.get(ct, 0))
        out.append({
            "candidate_class": ct,
            "attempted": max(attempted, gen),
            "generated": gen,
            "priced": int(priced.get(ct, 0)),
            "net_positive": int(net_positive.get(ct, 0)),
            "paper_candidate": int(paper.get(ct, 0)),
            "paper": int(paper.get(ct, 0)),
            "blocked_missing_ask": int(blocked_missing_ask.get(ct, 0)),
            "blocked_stale": int(blocked_stale.get(ct, 0)),
            "blocked_no_positive_net": int(blocked_no_positive_net.get(ct, 0)),
            "blocked_shape_or_time": int(blocked_shape_or_time.get(ct, 0)),
        })
    return out


def _coverage_class_for_candidate_type(candidate_type: Any) -> str:
    ct = str(candidate_type or "")
    if ct in {CT_MONOTONICITY, CT_THRESHOLD_TO_BUCKET, CT_BARRIER}:
        return "DIAGNOSTIC_ONLY_REQUIRES_SHORT" if ct == CT_THRESHOLD_TO_BUCKET else ct
    return ct


def _coverage_class_for_row(row: dict[str, Any]) -> str:
    if row.get("paper_candidate_class") == CLASS_CDNA or any(
        str(leg.get("platform") or "").lower() == "cdna" for leg in row.get("basket_legs") or []
    ):
        return "CDNA_FILL_FIRST"
    if row.get("requires_short_or_sell"):
        return "DIAGNOSTIC_ONLY_REQUIRES_SHORT"
    return _coverage_class_for_candidate_type(row.get("candidate_type"))


def _coverage_missing_ask_blocker(value: Any) -> bool:
    text = str(value or "")
    return text == "missing_ask" or (
        text.startswith("missing_")
        and (text.endswith("_ask") or "_display_" in text or text == "missing_cdna_display_price")
    )


def _primary_near_miss_reason(r: dict[str, Any]) -> str:
    """One normalized reason a buy-only row is not yet a candidate:
    ``missing_quote`` / ``stale_quote`` / ``negative_net`` / ``basis_buffer`` / ``other``."""
    labels = list(r.get("hard_blockers") or []) + list(r.get("quote_side_diagnostics") or [])
    if any(str(b) in _MISSING_ASK_LIKE for b in (r.get("hard_blockers") or [])) or any(
        str(x).startswith("missing_") for x in (r.get("quote_side_diagnostics") or [])
    ):
        return "missing_quote"
    if any("stale" in str(b) for b in labels):
        return "stale_quote"
    if "no_positive_adjusted_net_edge_after_basis_buffer" in labels:
        return "basis_buffer"
    net = r.get("net_edge_after_fees")
    if isinstance(net, (int, float)) and net <= 0:
        return "negative_net"
    return "other"


def _summary(rows: list[dict[str, Any]], grids: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(r.get("action") for r in rows)
    ctypes = Counter(r.get("candidate_type") for r in rows)
    paper_ctypes = Counter(r.get("candidate_type") for r in rows if r.get("paper_candidate"))
    classes = Counter(r.get("paper_candidate_class") for r in rows if r.get("paper_candidate"))
    # Separate blocker accounting so non-tradable (short-required) diagnostics do
    # NOT dominate the actionable buy-only dashboard.
    actionable = Counter()
    diagnostic = Counter()
    economic = Counter()
    for r in rows:
        hb = r.get("hard_blockers") or []
        if r.get("tradable_buy_only"):
            for b in hb:
                if b in ACTIONABLE_BUY_ONLY_BLOCKERS:
                    actionable[b] += 1
                if b in ECONOMIC_REJECTION_BLOCKERS:
                    economic[b] += 1
        else:
            for b in hb:
                if b in DIAGNOSTIC_ONLY_NON_TRADABLE_REASONS:
                    diagnostic[b] += 1
    diagnostic_short_rows = sum(1 for r in rows if r.get("requires_short_or_sell"))

    buy_only = [r for r in rows if r.get("tradable_buy_only")]
    near = sorted(
        [r for r in buy_only if _safe_float(r.get("net_edge_after_fees")) > -1e8],
        key=lambda r: _safe_float(r.get("net_edge_after_fees")), reverse=True,
    )
    top_buy_only_near_misses = [_near_miss_view(r) for r in near[:10]]
    # How many buy-only priced rows are within 2c / 5c / 10c of break-even (nested).
    near_buckets = {"within_2c": 0, "within_5c": 0, "within_10c": 0}
    for r in buy_only:
        net = r.get("net_edge_after_fees")
        if isinstance(net, (int, float)) and net <= 0:
            if net >= -0.02:
                near_buckets["within_2c"] += 1
            if net >= -0.05:
                near_buckets["within_5c"] += 1
            if net >= -0.10:
                near_buckets["within_10c"] += 1
    mono_one_leg_missing = sum(
        1 for r in rows
        if r.get("candidate_type") == CT_MONOTONICITY_COVER
        and (("missing_yes_lower_ask" in (r.get("hard_blockers") or [])) != ("missing_no_higher_ask" in (r.get("hard_blockers") or [])))
    )

    # Precise venue+side "which buy leg was missing" counts, buy-only rows only.
    quote_side_counts: Counter = Counter()
    for r in buy_only:
        for label in r.get("quote_side_diagnostics") or []:
            quote_side_counts[label] += 1
    near_miss_rows = sum(1 for r in rows if r.get("near_miss"))
    micro_test_rows = sum(1 for r in rows if r.get("manual_micro_test_candidate"))
    complement_rows = sum(1 for r in rows if r.get("complement_quote_used"))
    cdna_fill_first = [
        {
            "asset": r.get("asset"), "candidate_type": r.get("candidate_type"),
            "target_instant_utc": r.get("target_instant_utc"),
            "adjusted_net_edge_after_fees": r.get("adjusted_net_edge_after_fees"),
            "candidate_action": r.get("candidate_action"),
        }
        for r in rows
        if r.get("paper_candidate") and r.get("paper_candidate_class") == CLASS_CDNA
    ]

    fee_drag = sorted(
        (
            {
                "asset": r.get("asset"), "candidate_type": r.get("candidate_type"),
                "legs": len(r.get("basket_legs") or []), "total_cost_after_fees": r.get("total_cost_after_fees"),
                "net_edge_after_fees": r.get("net_edge_after_fees"),
            }
            for r in rows
            if r.get("net_edge_after_fees") is not None and r.get("net_edge_after_fees") <= 0
        ),
        key=lambda d: _safe_float(d.get("net_edge_after_fees")),
    )
    return {
        "rows": len(rows),
        "buy_only_rows": len(buy_only),
        "diagnostic_only_short_required_rows": diagnostic_short_rows,
        "state_grids_built": len(grids),
        "paper_candidate_rows": sum(1 for r in rows if r.get("paper_candidate")),
        "strict_paper_candidate_rows": classes.get(CLASS_STRICT, 0),
        "operator_paper_candidate_rows": classes.get(CLASS_OPERATOR, 0),
        "cdna_fill_first_paper_candidate_rows": classes.get(CLASS_CDNA, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "candidate_type_counts": dict(ctypes),
        "paper_candidate_type_counts": dict(paper_ctypes),
        "comparability_tier_counts": dict(Counter(r.get("comparability_tier") for r in rows)),
        # ``top_blockers`` is ACTIONABLE-only so requires_short never tops it.
        "top_blockers": [{"blocker": k, "count": v} for k, v in actionable.most_common(15)],
        "actionable_buy_only_blockers": dict(actionable),
        "diagnostic_only_non_tradable_reasons": dict(diagnostic),
        "economic_rejections": dict(economic),
        "top_buy_only_near_misses": top_buy_only_near_misses,
        "monotonicity_covers_one_leg_missing": mono_one_leg_missing,
        "near_miss_threshold_buckets": near_buckets,
        "quote_side_diagnostic_counts": dict(quote_side_counts),
        "near_miss_buy_only_rows": near_miss_rows,
        "manual_micro_test_candidate_rows": micro_test_rows,
        "complement_quote_rows": complement_rows,
        "cdna_fill_first_candidates": cdna_fill_first,
        "top_fee_drag_rows": fee_drag[:10],
    }


def _near_miss_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset": r.get("asset"),
        "candidate_type": r.get("candidate_type"),
        "paper_candidate": bool(r.get("paper_candidate")),
        "near_miss": bool(r.get("near_miss")),
        "near_miss_reason": r.get("near_miss_reason") or "",
        "near_miss_primary_reason": _primary_near_miss_reason(r),
        "missing_to_candidate": list(r.get("missing_to_candidate") or []),
        "manual_micro_test_candidate": bool(r.get("manual_micro_test_candidate")),
        "complement_quote_used": bool(r.get("complement_quote_used")),
        "net_edge_after_fees": r.get("net_edge_after_fees"),
        "adjusted_net_edge_after_fees": r.get("adjusted_net_edge_after_fees"),
        "lower_strike": r.get("lower_strike"),
        "higher_strike": r.get("higher_strike"),
        "target_instant_utc": r.get("target_instant_utc"),
        "quote_side_diagnostics": list(r.get("quote_side_diagnostics") or []),
        "hard_blockers": list(r.get("hard_blockers") or []),
    }


def _basis_buffer_sensitivity(rows: list[dict[str, Any]], buffer_edge: float) -> dict[str, Any]:
    """How candidate counts respond to the source-basis buffer: rows positive
    before fees-buffer vs after, and which would be removed by the current buffer."""
    cross = [
        r for r in rows
        if r.get("net_edge_after_fees") is not None and "source_index_mismatch" in (r.get("assumptions_accepted") or [])
        or "source_index_mismatch" in (r.get("hard_blockers") or [])
    ]
    positive_net = [r for r in rows if (r.get("net_edge_after_fees") or -9) > 0]
    positive_adjusted = [r for r in rows if (r.get("adjusted_net_edge_after_fees") or -9) > 0]
    removed = [
        {
            "asset": r.get("asset"), "candidate_type": r.get("candidate_type"),
            "net_edge_after_fees": r.get("net_edge_after_fees"),
            "adjusted_net_edge_after_fees": r.get("adjusted_net_edge_after_fees"),
        }
        for r in rows
        if (r.get("net_edge_after_fees") or -9) > 0 and (r.get("adjusted_net_edge_after_fees") or -9) <= 0
    ]
    return {
        "source_basis_buffer_edge": buffer_edge,
        "rows_positive_net_before_buffer": len(positive_net),
        "rows_positive_after_buffer": len(positive_adjusted),
        "rows_removed_by_buffer": len(removed),
        "removed_examples": removed[:10],
    }


# ---------------------------------------------------------------------------- #
# Markdown                                                                       #
# ---------------------------------------------------------------------------- #


def render_crypto_structural_payoff_arb_scout_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    paper = [r for r in rows if r.get("paper_candidate")]

    def _by_type(ct: str) -> list[dict[str, Any]]:
        return [r for r in rows if r.get("candidate_type") == ct]

    bbs = report.get("basis_buffer_sensitivity") or {}
    lines = [
        "# Crypto Structural Payoff-State Arb Scout",
        "",
        "Contract-grammar-aware payoff-state engine. Classifies grammar first "
        "(terminal_threshold / terminal_range / directional_return / barrier_touch), then for each "
        "asset + target instant + compatible family builds a terminal-price payoff vector and "
        "searches structural opportunities. Asks only; no midpoint; YES-only buckets; "
        "settlement-instant and family discipline preserved.",
        "",
        "## 1. Summary",
        "",
        f"- operator_risk_mode: `{_md(report.get('operator_risk_mode'))}`  assets: `{', '.join(report.get('assets_requested') or [])}`",
        f"- load source: `{_md((report.get('load_diagnostics') or {}).get('source'))}`  "
        f"assets_loaded: `{_md(', '.join((report.get('load_diagnostics') or {}).get('assets_loaded') or []) or 'none')}`",
        f"- contract_grammar_counts: `{_md(_fmt_counter(report.get('contract_grammar_counts') or {}))}`",
        f"- state_grids_built: `{counts.get('state_grids_built', 0)}`  rows: `{counts.get('rows', 0)}`  "
        f"paper_candidate_rows: `{counts.get('paper_candidate_rows', 0)}`",
        f"- tradable_buy_only_rows: `{counts.get('buy_only_rows', 0)}`  "
        f"diagnostic_only_short_required_rows: `{counts.get('diagnostic_only_short_required_rows', 0)}` "
        f"(excluded from actionable/paper pressure — Mason cannot short)",
        f"- candidate_type_counts: `{_md(_fmt_counter(counts.get('candidate_type_counts') or {}))}`",
        f"- comparability_tier_counts: `{_md(_fmt_counter(counts.get('comparability_tier_counts') or {}))}`",
        f"- paper by class: strict=`{counts.get('strict_paper_candidate_rows', 0)}` "
        f"operator=`{counts.get('operator_paper_candidate_rows', 0)}` "
        f"cdna_fill_first=`{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- source_basis_buffer_bps: `{report.get('source_basis_buffer_bps', 0)}` "
        f"(edge `{report.get('source_basis_buffer_edge', 0)}`)  "
        f"absolute: `{_md(_fmt_counter(report.get('source_basis_buffer_absolute') or {}) )}`",
        "",
        "## 1b. Candidate Generation Coverage",
        "",
        "Per class: attempted (structural opportunities evaluated) -> generated (rows) -> "
        "priced (computable net) -> paper. A big attempted>generated gap means the class IS "
        "evaluated but sampled out / not yet quotable — not that it was never tried.",
        "",
        "| Candidate class | Attempted | Generated | Priced | Net+ | Paper | Missing ask | Stale | No positive net | Shape/time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    cgc = report.get("candidate_generation_coverage") or counts.get("candidate_generation_coverage") or []
    if not cgc:
        lines.append("| none | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    for c in cgc:
        lines.append(
            f"| {_md(c.get('candidate_class'))} | {_md(c.get('attempted', 0))} | {_md(c.get('generated', 0))} | "
            f"{_md(c.get('priced', 0))} | {_md(c.get('net_positive', 0))} | "
            f"{_md(c.get('paper_candidate', c.get('paper', 0)))} | {_md(c.get('blocked_missing_ask', 0))} | "
            f"{_md(c.get('blocked_stale', 0))} | {_md(c.get('blocked_no_positive_net', 0))} | "
            f"{_md(c.get('blocked_shape_or_time', 0))} |"
        )
    nb = counts.get("near_miss_threshold_buckets") or report.get("near_miss_threshold_buckets") or {}
    qcov = report.get("quote_coverage_diagnostics") or counts.get("quote_coverage_diagnostics") or {}
    raw_qcov = qcov.get("raw") or {}
    usable_qcov = qcov.get("usable") or {}
    lines.extend([
        "",
        "## 1c. Raw vs Usable Quote Coverage",
        "",
        "| Metric | Raw | Usable |",
        "|---|---:|---:|",
    ])
    for key in QUOTE_COVERAGE_SIDE_KEYS:
        lines.append(f"| {_md(key)} | {_md(raw_qcov.get(key, 0))} | {_md(usable_qcov.get(key, 0))} |")
    lines.extend([
        "",
        f"- complement_quote_used_count: `{qcov.get('complement_quote_used_count', 0)}`",
        f"- complement_quote_possible_but_missing_bid: `{qcov.get('complement_quote_possible_but_missing_bid', 0)}`",
        f"- explicit_ask_used_count: `{qcov.get('explicit_ask_used_count', 0)}`",
        f"- gamma_top_of_book_fallback_count: `{qcov.get('gamma_top_of_book_fallback_count', 0)}`",
        f"- clob_book_used_count: `{qcov.get('clob_book_used_count', 0)}`",
    ])
    uda = report.get("up_down_audit") or counts.get("up_down_audit") or {}
    lines.extend([
        "",
        "## 1d. Direct Up/Down Window Audit",
        "",
        f"- up_down_kalshi_rows: `{uda.get('up_down_kalshi_rows', 0)}`",
        f"- up_down_polymarket_rows: `{uda.get('up_down_polymarket_rows', 0)}`",
        f"- up_down_exact_window_matches: `{uda.get('up_down_exact_window_matches', 0)}`",
        f"- up_down_candidates_generated: `{uda.get('up_down_candidates_generated', 0)}`",
        f"- up_down_paper_candidates: `{uda.get('up_down_paper_candidates', 0)}`",
        f"- warning: `{_md(uda.get('warning') or 'none')}`",
        "",
        "| Sample | Platform | Start | End | Interval seconds |",
        "|---|---|---|---|---:|",
    ])
    samples = (uda.get("sample_kalshi_windows") or [])[:3] + (uda.get("sample_polymarket_windows") or [])[:3]
    if not samples:
        lines.append("| none |  |  |  |  |")
    for sample in samples:
        lines.append(
            f"| {_md(sample.get('asset'))} | {_md(sample.get('platform'))} | "
            f"{_md(sample.get('reference_start_utc'))} | {_md(sample.get('target_instant_utc'))} | "
            f"{_md(sample.get('interval_length_seconds'))} |"
        )
    lines.extend([
        "",
        f"- near-miss buckets (buy-only, below break-even): within_2c=`{nb.get('within_2c', 0)}`  "
        f"within_5c=`{nb.get('within_5c', 0)}`  within_10c=`{nb.get('within_10c', 0)}`",
        "",
        "## 2. Paper Candidates (sorted by adjusted net edge after fees)",
        "",
        "| Class | Tier | Type | Asset | Instant (UTC) | Legs | Net edge | Adj net | Size/cap | Assumptions | Candidate action |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
    ])
    if not paper:
        lines.append("| none |  |  |  |  |  |  |  |  |  |  |")
    for r in paper[:50]:
        lines.append(_paper_md(r))

    lines.extend(["", "## 3. Contract Grammar Coverage", "", "| Family | Markets | Note |", "|---|---:|---|"])
    fam_notes = {
        CONTRACT_FAMILY_TERMINAL_THRESHOLD: "above/below K at T (terminal price)",
        CONTRACT_FAMILY_TERMINAL_RANGE: "between L and U at T (terminal price)",
        CONTRACT_FAMILY_DIRECTIONAL_RETURN: "up/down vs reference; needs same start+window",
        CONTRACT_FAMILY_BARRIER_TOUCH: "path-dependent; never mixed with terminal/up-down",
        CONTRACT_FAMILY_UNKNOWN: "unclassified",
    }
    gc = report.get("contract_grammar_counts") or {}
    if not gc:
        lines.append("| none | 0 |  |")
    for fam, n in sorted(gc.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {_md(fam)} | {_md(n)} | {_md(fam_notes.get(fam, ''))} |")

    def _section(title: str, sub: list[dict[str, Any]]) -> None:
        lines.extend(["", title, "", "| Action | Tier | Asset | Instant | Legs | Net edge | Adj net | Blockers |", "|---|---|---|---|---:|---:|---:|---|"])
        if not sub:
            lines.append("| none |  |  |  |  |  |  |  |")
        for r in sub[:40]:
            lines.append(
                "| "
                f"{_md(r.get('action'))} | {_md(r.get('comparability_tier'))} | {_md(r.get('asset'))} | "
                f"{_md(r.get('target_instant_utc'))} | {len(r.get('basket_legs') or [])} | "
                f"{_md(r.get('net_edge_after_fees'))} | {_md(r.get('adjusted_net_edge_after_fees'))} | "
                f"{_md(', '.join(r.get('hard_blockers') or []))} |"
            )

    _section("## 4. Long-only guaranteed payoff baskets", _by_type(CT_LONG_ONLY))
    _section(
        "## 5. Terminal threshold/range candidates",
        [r for r in rows if r.get("candidate_type") in (CT_CROSS_VENUE, CT_SAME_PAYOFF_CHEAPER) and str(r.get("contract_family")).startswith("terminal")],
    )

    # Threshold monotonicity covers: YES(>L) + NO(>U).
    mcd = report.get("monotonicity_cover_diagnostics") or {}
    mono = _by_type(CT_MONOTONICITY_COVER)
    mono.sort(key=lambda r: (1 if r.get("paper_candidate") else 0, _safe_float(r.get("net_edge_after_fees"))), reverse=True)
    lines.extend(
        [
            "",
            "## 5b. Threshold Monotonicity Cover Candidates",
            "",
            f"- monotonicity_pairs_checked: `{mcd.get('monotonicity_pairs_checked', 0)}`  "
            f"candidates_generated: `{mcd.get('monotonicity_cover_candidates_generated', 0)}`  "
            f"paper_candidates: `{mcd.get('monotonicity_cover_paper_candidates', 0)}`",
            f"- missing_yes_lower_ask: `{mcd.get('missing_yes_lower_ask', 0)}`  "
            f"missing_no_higher_ask: `{mcd.get('missing_no_higher_ask', 0)}`  "
            f"complement_quote_used: `{mcd.get('complement_quote_used', 0)}`",
            "",
            "| Action | Asset | Instant | Lower K | Higher K | YES lower ask | NO higher ask | Total cost | Min | Max | Net edge | Size/cap | Class | Assumptions | Blockers |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    if not mono:
        lines.append("| none |  |  |  |  |  |  |  |  |  |  |  |  |  |  |")
    for r in mono[:50]:
        lines.append(
            "| "
            f"{_md(r.get('action'))} | {_md(r.get('asset'))} | {_md(r.get('target_instant_utc'))} | "
            f"{_md(r.get('lower_strike'))} | {_md(r.get('higher_strike'))} | {_md(r.get('yes_lower_ask'))} | "
            f"{_md(r.get('no_higher_ask'))} | {_md(r.get('total_cost_after_fees'))} | {_md(r.get('min_payoff'))} | "
            f"{_md(r.get('max_payoff'))} | {_md(r.get('net_edge_after_fees'))} | {_md(r.get('available_size_or_cap'))} | "
            f"{_md(r.get('paper_candidate_class'))} | {_md(', '.join(r.get('assumptions_accepted') or []))} | "
            f"{_md(', '.join(r.get('hard_blockers') or []))} |"
        )

    _section("## 6. Directional up/down same-window candidates", _by_type(CT_UP_DOWN))

    lines.extend(["", "## 7. CDNA fill-first candidates", "", "| Type | Asset | Instant | Legs | Adj net | Candidate action |", "|---|---|---|---:|---:|---|"])
    cdna = [r for r in rows if r.get("paper_candidate_class") == CLASS_CDNA and r.get("paper_candidate")]
    if not cdna:
        lines.append("| none |  |  |  |  |  |")
    for r in cdna[:25]:
        lines.append(
            f"| {_md(r.get('candidate_type'))} | {_md(r.get('asset'))} | {_md(r.get('target_instant_utc'))} | "
            f"{len(r.get('basket_legs') or [])} | {_md(r.get('adjusted_net_edge_after_fees'))} | {_md(r.get('candidate_action'))} |"
        )

    _section("## 8. Synthetic range/bucket/threshold candidates", _by_type(CT_BUCKET_TO_THRESHOLD))

    lines.extend(["", "## 9. Diagnostic-only monotonicity and threshold-to-range rows", "", "| Type | Asset | Instant | Note |", "|---|---|---|---|"])
    diag = [r for r in rows if r.get("candidate_type") in (CT_MONOTONICITY, CT_THRESHOLD_TO_BUCKET) or r.get("lane") == "barrier"]
    if not diag:
        lines.append("| none |  |  |  |")
    for r in diag[:40]:
        lines.append(
            f"| {_md(r.get('candidate_type'))} | {_md(r.get('asset'))} | {_md(r.get('target_instant_utc'))} | "
            f"{_md('; '.join(r.get('risk_notes') or []))} |"
        )

    lines.extend(["", "## 10. Fee-drag rejected baskets (worst net after fees)", "", "| Asset | Type | Legs | Total cost | Net edge |", "|---|---|---:|---:|---:|"])
    fd = counts.get("top_fee_drag_rows") or []
    if not fd:
        lines.append("| none |  |  |  |  |")
    for d in fd[:10]:
        lines.append(
            f"| {_md(d.get('asset'))} | {_md(d.get('candidate_type'))} | {_md(d.get('legs'))} | "
            f"{_md(d.get('total_cost_after_fees'))} | {_md(d.get('net_edge_after_fees'))} |"
        )

    lines.extend(
        [
            "",
            "## 11. Basis-buffer sensitivity",
            "",
            f"- source_basis_buffer_edge: `{bbs.get('source_basis_buffer_edge', 0)}`",
            f"- rows_positive_net_before_buffer: `{bbs.get('rows_positive_net_before_buffer', 0)}`  "
            f"rows_positive_after_buffer: `{bbs.get('rows_positive_after_buffer', 0)}`  "
            f"rows_removed_by_buffer: `{bbs.get('rows_removed_by_buffer', 0)}`",
            "",
            "| Asset | Type | Net edge | Adjusted net |",
            "|---|---|---:|---:|",
        ]
    )
    if not bbs.get("removed_examples"):
        lines.append("| none |  |  |  |")
    for d in bbs.get("removed_examples") or []:
        lines.append(
            f"| {_md(d.get('asset'))} | {_md(d.get('candidate_type'))} | {_md(d.get('net_edge_after_fees'))} | "
            f"{_md(d.get('adjusted_net_edge_after_fees'))} |"
        )

    def _blocker_table(title: str, note: str, counter: dict[str, int]) -> None:
        lines.extend(["", title, "", note, "", "| Blocker | Count |", "|---|---:|"])
        items = sorted((counter or {}).items(), key=lambda kv: (-kv[1], kv[0]))
        if not items:
            lines.append("| none | 0 |")
        for k, v in items[:15]:
            lines.append(f"| {_md(k)} | {_md(v)} |")

    _blocker_table(
        "## 12a. Actionable Buy-Only Blockers",
        "Blockers on tradable buy-only rows only. Shorting-required diagnostics are excluded so they never dominate.",
        report.get("actionable_buy_only_blockers") or {},
    )
    _blocker_table(
        "## 12b. Diagnostic-Only Non-Tradable Reasons",
        "Why non-tradable rows are diagnostic-only (e.g. requires_short_or_not_guaranteed). Not actionable; never paper candidates.",
        report.get("diagnostic_only_non_tradable_reasons") or {},
    )
    _blocker_table(
        "## 12c. Economic Rejections",
        "Buy-only rows that priced but lost edge to fees / basis buffer.",
        report.get("economic_rejections") or {},
    )

    lines.extend(
        [
            "",
            "## 12d. Diagnostic Only: Requires Shorting / Not Tradable Buy-Only",
            "",
            "Mason cannot short; these rows are informational only and are excluded from actionable "
            "and paper-blocker pressure.",
            "",
            f"- diagnostic_only_short_required_rows: `{counts.get('diagnostic_only_short_required_rows', 0)}`",
            "",
            "| Type | Asset | Instant | Lower K | Higher K | Reason |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    short_rows = [r for r in rows if r.get("requires_short_or_sell")]
    if not short_rows:
        lines.append("| none |  |  |  |  |  |")
    for r in short_rows[:40]:
        reasons = [b for b in (r.get("hard_blockers") or []) if b in _REQUIRES_SHORT_BLOCKERS] or ["requires_short_or_not_guaranteed"]
        lines.append(
            f"| {_md(r.get('candidate_type'))} | {_md(r.get('asset'))} | {_md(r.get('target_instant_utc'))} | "
            f"{_md(r.get('lower_strike'))} | {_md(r.get('higher_strike'))} | {_md(', '.join(reasons))} |"
        )

    near = counts.get("top_buy_only_near_misses") or []
    lines.extend(
        [
            "",
            "## 12e. Top Buy-Only Near-Misses (by net edge after fees)",
            "",
            f"- buy_only_rows: `{counts.get('buy_only_rows', 0)}`  "
            f"near_miss_buy_only_rows: `{counts.get('near_miss_buy_only_rows', 0)}`  "
            f"manual_micro_test_candidate_rows: `{counts.get('manual_micro_test_candidate_rows', 0)}`  "
            f"monotonicity_covers_one_leg_missing: `{counts.get('monotonicity_covers_one_leg_missing', 0)}`",
            "",
            "| Asset | Type | Paper | Net edge | Adj net | Near-miss reason | Missing to candidate |",
            "|---|---|---|---:|---:|---|---|",
        ]
    )
    if not near:
        lines.append("| none |  |  |  |  |  |  |")
    for d in near[:10]:
        lines.append(
            f"| {_md(d.get('asset'))} | {_md(d.get('candidate_type'))} | "
            f"{_md('yes' if d.get('paper_candidate') else 'no')} | {_md(d.get('net_edge_after_fees'))} | "
            f"{_md(d.get('adjusted_net_edge_after_fees'))} | {_md(d.get('near_miss_reason') or '')} | "
            f"{_md('; '.join(d.get('missing_to_candidate') or []) or ', '.join(d.get('hard_blockers') or []))} |"
        )

    qsd = counts.get("quote_side_diagnostic_counts") or {}
    lines.extend(
        [
            "",
            "## 12f. Missing Buy-Side Quote Diagnostics (venue + side)",
            "",
            "Exactly which buy leg prevented pricing, on buy-only rows. `complement_quote_used:*` "
            "means a NO/YES ask was derived from the opposite executable bid (never a midpoint).",
            "",
            "| Diagnostic | Count |",
            "|---|---:|",
        ]
    )
    qsd_items = sorted(qsd.items(), key=lambda kv: (-kv[1], kv[0]))
    if not qsd_items:
        lines.append("| none | 0 |")
    for k, v in qsd_items[:20]:
        lines.append(f"| {_md(k)} | {_md(v)} |")

    cdna_cands = counts.get("cdna_fill_first_candidates") or []
    lines.extend(
        [
            "",
            "## 12g. CDNA Fill-First Candidates",
            "",
            "| Asset | Type | Instant | Adj net | Candidate action |",
            "|---|---|---|---:|---|",
        ]
    )
    if not cdna_cands:
        lines.append("| none |  |  |  |  |")
    for d in cdna_cands[:15]:
        lines.append(
            f"| {_md(d.get('asset'))} | {_md(d.get('candidate_type'))} | {_md(d.get('target_instant_utc'))} | "
            f"{_md(d.get('adjusted_net_edge_after_fees'))} | {_md(d.get('candidate_action'))} |"
        )

    lines.extend(
        [
            "",
            "## 13. Safety",
            "",
            "- diagnostic_only: `true`",
            "- public_read_only: `true`",
            "- cdna_network_fetch_attempted: `false`",
            "- uses_asks_for_entry: `true`  uses_midpoint: `false`",
            "- synthetic_uses_yes_buckets_only: `true`",
            "- orders_or_execution_logic_added: `false`",
            "- auth_or_account_logic_added: `false`",
            "- browser_automation_added: `false`",
            "- strict_exact_arb (engine-wide): `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _paper_md(r: dict[str, Any]) -> str:
    return (
        "| "
        f"{_md(r.get('paper_candidate_class'))} | {_md(r.get('comparability_tier'))} | {_md(r.get('candidate_type'))} | "
        f"{_md(r.get('asset'))} | {_md(r.get('target_instant_utc'))} | {len(r.get('basket_legs') or [])} | "
        f"{_md(r.get('net_edge_after_fees'))} | {_md(r.get('adjusted_net_edge_after_fees'))} | "
        f"{_md(r.get('available_size_or_cap'))} | {_md(', '.join(r.get('assumptions_accepted') or []))} | "
        f"{_md(r.get('candidate_action'))} |"
    )


def _fmt_counter(counter: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
