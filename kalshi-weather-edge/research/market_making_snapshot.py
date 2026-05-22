from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import PROJECT_ROOT
from data.storage import Storage
from research.market_making_analysis import _market_likely_expired

SCHEMA_KIND = "market_making_snapshot_v1"
SCHEMA_VERSION = 1
SUPPORTED_VENUES = {"kalshi"}


@dataclass(frozen=True)
class MarketMakingSnapshotConfig:
    venue: str = "kalshi"
    max_output_markets: int = 1000


@dataclass(frozen=True)
class MarketMakingSnapshotResult:
    payload: dict[str, Any]
    markdown: str
    exports: dict[str, str] | None = None

    def to_text(self) -> str:
        summary = self.payload["summary"]
        lines = [
            f"market_making_snapshot_status={summary.get('status')}",
            f"venue={self.payload.get('venue_id')} research_only={str(self.payload.get('research_only')).lower()} "
            f"execution_enabled={str(self.payload.get('execution_enabled')).lower()}",
            f"markets={summary.get('total_markets')} two_sided={summary.get('markets_with_two_sided_books')} "
            f"bid_ask={summary.get('markets_with_bid_ask')} depth={summary.get('markets_with_depth')} "
            f"trade_prints={summary.get('markets_with_trade_print_evidence')} "
            f"trade_print_scope={summary.get('trade_print_evidence_count_scope')}",
            f"stale_post_event_risk={summary.get('stale_post_event_risk_count')} "
            f"event_drift_risk={summary.get('event_drift_risk_count')} "
            f"fee_model_missing={summary.get('fee_model_missing_count')} "
            f"quote_freshness_available={summary.get('quote_freshness_available_count')} "
            f"quote_freshness_missing={summary.get('quote_freshness_missing_count')}",
            f"exports={self.exports}",
            "Top research-only examples:",
        ]
        for row in self.payload.get("markets", [])[:10]:
            lines.append(
                f"- {row.get('ticker')} category={row.get('category')} status={row.get('market_status')} "
                f"bid={row.get('bid_ask', {}).get('yes_bid')} ask={row.get('bid_ask', {}).get('yes_ask')} "
                f"spread={row.get('spread_cents')} trades={row.get('trade_print_evidence_summary', {}).get('trade_count')} "
                f"blockers={','.join(row.get('missing_fields_blocking_paper_market_making') or []) or 'none'}"
            )
        return "\n".join(lines)


class MarketMakingSnapshotBuilder:
    """Build a venue-agnostic market-making snapshot from local research data.

    This is read-only. It does not fetch live APIs, place orders, read account
    state, or promote paper/live readiness.
    """

    def __init__(self, storage: Storage | None = None, config: MarketMakingSnapshotConfig | None = None):
        self.storage = storage or Storage()
        self.config = config or MarketMakingSnapshotConfig()

    def build(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        last_days: int | None = None,
        persist_exports: bool = True,
    ) -> MarketMakingSnapshotResult:
        venue = self.config.venue.lower().strip()
        if venue not in SUPPORTED_VENUES:
            raise ValueError(f"unsupported market-making snapshot venue: {self.config.venue}")
        start, end = _date_window(start, end, last_days)
        generated_at = datetime.now(timezone.utc)
        full_counts = _full_summary_counts(self.storage, start, end)
        all_markets = _build_kalshi_markets(self.storage, start, end, max_output_markets=self.config.max_output_markets)
        max_output = max(int(self.config.max_output_markets), 1)
        markets = all_markets[:max_output]
        summary = _summary(all_markets, start, end, full_counts)
        summary["serialized_market_count"] = len(markets)
        summary["market_rows_truncated"] = int(full_counts.get("total_markets") or 0) > len(markets)
        payload = {
            "schema_kind": SCHEMA_KIND,
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at.isoformat(),
            "venue_id": "kalshi",
            "source_provenance": {
                "source": "local_db",
                "orderbook_table": "orderbook_snapshots_live",
                "trade_print_table": "historical_trades",
                "live_api_fetch_attempted": False,
                "account_or_order_access_attempted": False,
                "window_start": start.isoformat() if start else None,
                "window_end": end.isoformat() if end else None,
            },
            "research_only": True,
            "execution_enabled": False,
            "readiness_promotion": "none",
            "paper_candidate_allowed_default": False,
            "summary": summary,
            "markets": markets,
            "disclaimer": (
                "Venue-agnostic market-making snapshot is local, read-only research data. "
                "It is not paper/live readiness, not a trading signal, and not an executable-liquidity claim."
            ),
        }
        markdown = _markdown(payload)
        exports = _export(payload, markdown) if persist_exports else None
        return MarketMakingSnapshotResult(payload=payload, markdown=markdown, exports=exports)


def _date_window(start: date | None, end: date | None, last_days: int | None) -> tuple[date | None, date | None]:
    if start or end:
        return start, end
    if last_days is None:
        return None, None
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=max(int(last_days), 1)), today


def _time_where(start: date | None, end: date | None, column: str = "ts") -> tuple[str, dict[str, str]]:
    clauses = ["1=1"]
    params: dict[str, str] = {}
    if start:
        clauses.append(f"{column} >= :start_ts")
        params["start_ts"] = start.isoformat()
    if end:
        clauses.append(f"{column} < :end_ts")
        params["end_ts"] = (end + timedelta(days=1)).isoformat()
    return " AND ".join(clauses), params


def _full_summary_counts(storage: Storage, start: date | None, end: date | None) -> dict[str, Any]:
    where, params = _time_where(start, end, "ts")
    book_counts = storage.fetch_sql(
        f"""
        SELECT
            COUNT(DISTINCT market_ticker) AS total_markets,
            COUNT(DISTINCT CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL THEN market_ticker END) AS markets_with_bid_ask,
            COUNT(DISTINCT CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL AND spread_cents IS NOT NULL THEN market_ticker END) AS markets_with_two_sided_books,
            COUNT(DISTINCT CASE WHEN COALESCE(depth_yes_bid_1, 0) > 0 OR COALESCE(depth_yes_ask_1, 0) > 0 OR COALESCE(depth_no_bid_1, 0) > 0 OR COALESCE(depth_no_ask_1, 0) > 0 THEN market_ticker END) AS markets_with_depth,
            COUNT(DISTINCT CASE WHEN ts IS NOT NULL THEN market_ticker END) AS quote_freshness_available_count
        FROM orderbook_snapshots_live
        WHERE {where}
        """,
        params,
    )
    row = book_counts.iloc[0].to_dict() if not book_counts.empty else {}
    category_counts = _full_category_counts(storage, start, end)
    row["category_counts"] = category_counts
    return {
        "total_markets": _int(row.get("total_markets")),
        "markets_with_bid_ask": _int(row.get("markets_with_bid_ask")),
        "markets_with_two_sided_books": _int(row.get("markets_with_two_sided_books")),
        "markets_with_depth": _int(row.get("markets_with_depth")),
        "markets_with_trade_print_evidence": None,
        "trade_print_evidence_count_scope": "serialized_market_rows",
        "quote_freshness_available_count": _int(row.get("quote_freshness_available_count")),
        "category_counts": category_counts,
    }


def _full_category_counts(storage: Storage, start: date | None, end: date | None) -> dict[str, int]:
    where, params = _time_where(start, end, "ts")
    frame = storage.fetch_sql(f"SELECT DISTINCT market_ticker FROM orderbook_snapshots_live WHERE {where}", params)
    counts: dict[str, int] = {}
    if frame.empty:
        return counts
    for ticker in frame["market_ticker"].dropna().astype(str):
        category = _category_for_ticker(ticker)
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def _build_kalshi_markets(storage: Storage, start: date | None, end: date | None, *, max_output_markets: int) -> list[dict[str, Any]]:
    where, params = _time_where(start, end, "ts")
    sample_limit = max(int(max_output_markets) * 25, int(max_output_markets), 1000)
    sample = storage.fetch_sql(
        f"""
        SELECT
            market_ticker, ts, yes_best_bid, yes_best_ask, no_best_bid, no_best_ask,
            spread_cents, mid_cents, depth_yes_bid_1, depth_yes_ask_1,
            depth_no_bid_1, depth_no_ask_1, total_yes_bid_depth, total_no_bid_depth,
            last_price_cents, volume, volume_24h, open_interest, liquidity_cents,
            market_status, market_close_time, source
        FROM orderbook_snapshots_live
        WHERE {where}
        ORDER BY ts DESC
        LIMIT :sample_limit
        """,
        {**params, "sample_limit": sample_limit},
    )
    if sample.empty:
        return []
    books = (
        sample.sort_values("ts", ascending=False)
        .drop_duplicates(subset=["market_ticker"], keep="first")
        .head(max(int(max_output_markets), 1))
        .copy()
    )
    if books.empty:
        return []
    tickers = [str(value) for value in books["market_ticker"].dropna().unique().tolist()]
    stats = _book_stats_for_tickers(storage, start, end, tickers)
    if books.empty:
        return []
    trades = _trade_summary(storage, start, end, tickers)
    rows: list[dict[str, Any]] = []
    for _, row in books.iterrows():
        ticker = str(row.get("market_ticker") or "")
        trade = trades.get(ticker, {})
        stat = stats.get(ticker, {})
        category = _category_for_ticker(ticker)
        likely_expired = _likely_expired_from_row(row)
        unresolved_risks = _unresolved_risks(row, trade, likely_expired, category)
        blockers = _paper_blockers(row, trade, likely_expired)
        rows.append(
            {
                "venue_id": "kalshi",
                "market_id": ticker,
                "ticker": ticker,
                "title": None,
                "question": None,
                "category": category,
                "market_status": _none_if_nan(row.get("market_status")) or "unknown",
                "event_time": None,
                "close_time": _iso_or_none(row.get("market_close_time")),
                "settlement_time": None,
                "bid_ask": {
                    "yes_bid": _num(row.get("yes_best_bid")),
                    "yes_ask": _num(row.get("yes_best_ask")),
                    "no_bid": _num(row.get("no_best_bid")),
                    "no_ask": _num(row.get("no_best_ask")),
                },
                "spread_cents": _num(row.get("spread_cents")),
                "depth": {
                    "yes_bid_1": _num(row.get("depth_yes_bid_1")),
                    "yes_ask_1": _num(row.get("depth_yes_ask_1")),
                    "no_bid_1": _num(row.get("depth_no_bid_1")),
                    "no_ask_1": _num(row.get("depth_no_ask_1")),
                    "total_yes_bid_depth": _num(row.get("total_yes_bid_depth")),
                    "total_no_bid_depth": _num(row.get("total_no_bid_depth")),
                },
                "quote_timestamp": _iso_or_none(row.get("latest_snapshot_at")),
                "first_snapshot_at": stat.get("first_snapshot_at"),
                "latest_snapshot_at": _iso_or_none(row.get("ts")),
                "snapshot_count": int(stat.get("snapshot_count") or 0),
                "two_sided_book_snapshot_count": int(stat.get("two_sided_snapshot_count") or 0),
                "bid_ask_snapshot_count": int(stat.get("bid_ask_snapshot_count") or 0),
                "depth_snapshot_count": int(stat.get("depth_snapshot_count") or 0),
                "trade_print_evidence_summary": {
                    "trade_count": int(trade.get("trade_count") or 0),
                    "latest_trade_at": trade.get("latest_trade_at"),
                    "total_trade_size": float(trade.get("total_trade_size") or 0.0),
                    "source": "historical_trades",
                },
                "fee_model_status": "configured_in_analysis_not_serialized",
                "min_tick": 1,
                "contract_unit": "kalshi_contract",
                "stale_post_event_risk_flags": ["likely_expired_or_post_event"] if likely_expired else [],
                "event_drift_risk_flags": _event_drift_flags(category, likely_expired),
                "source_provenance": {
                    "orderbook_source": _none_if_nan(row.get("source")) or "orderbook_snapshots_live",
                    "trade_print_source": "historical_trades",
                    "local_db_only": True,
                },
                "restrictions": [
                    "read_only_research_snapshot",
                    "no_order_or_account_access",
                    "no_live_readiness_promotion",
                ],
                "unresolved_risks": unresolved_risks,
                "missing_fields_blocking_paper_market_making": blockers,
                "paper_candidate_allowed": False,
                "research_only": True,
                "execution_enabled": False,
            }
        )
    return rows


def _book_stats_for_tickers(storage: Storage, start: date | None, end: date | None, tickers: list[str]) -> dict[str, dict[str, Any]]:
    if not tickers:
        return {}
    where, params = _time_where(start, end, "ts")
    ticker_placeholders = {f"ticker_{idx}": ticker for idx, ticker in enumerate(tickers)}
    in_clause = ", ".join(f":{key}" for key in ticker_placeholders)
    frame = storage.fetch_sql(
        f"""
        SELECT
            market_ticker,
            COUNT(*) AS snapshot_count,
            MIN(ts) AS first_snapshot_at,
            SUM(CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL THEN 1 ELSE 0 END) AS bid_ask_snapshot_count,
            SUM(CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL AND spread_cents IS NOT NULL THEN 1 ELSE 0 END) AS two_sided_snapshot_count,
            SUM(CASE WHEN COALESCE(depth_yes_bid_1, 0) > 0 OR COALESCE(depth_yes_ask_1, 0) > 0 OR COALESCE(depth_no_bid_1, 0) > 0 OR COALESCE(depth_no_ask_1, 0) > 0 THEN 1 ELSE 0 END) AS depth_snapshot_count
        FROM orderbook_snapshots_live
        WHERE {where} AND market_ticker IN ({in_clause})
        GROUP BY market_ticker
        """,
        {**params, **ticker_placeholders},
    )
    if frame.empty:
        return {}
    return {
        str(row["market_ticker"]): {
            "snapshot_count": _int(row.get("snapshot_count")),
            "first_snapshot_at": _iso_or_none(row.get("first_snapshot_at")),
            "bid_ask_snapshot_count": _int(row.get("bid_ask_snapshot_count")),
            "two_sided_snapshot_count": _int(row.get("two_sided_snapshot_count")),
            "depth_snapshot_count": _int(row.get("depth_snapshot_count")),
        }
        for _, row in frame.iterrows()
    }


def _trade_summary(storage: Storage, start: date | None, end: date | None, tickers: list[str]) -> dict[str, dict[str, Any]]:
    if not tickers:
        return {}
    where, params = _time_where(start, end, "ts")
    ticker_placeholders = {f"ticker_{idx}": ticker for idx, ticker in enumerate(tickers)}
    in_clause = ", ".join(f":{key}" for key in ticker_placeholders)
    frame = storage.fetch_sql(
        f"""
        SELECT
            market_ticker,
            COUNT(*) AS trade_count,
            MAX(ts) AS latest_trade_at,
            SUM(COALESCE(count, 0)) AS total_trade_size
        FROM historical_trades
        WHERE {where} AND market_ticker IN ({in_clause})
        GROUP BY market_ticker
        """,
        {**params, **ticker_placeholders},
    )
    if frame.empty:
        return {}
    return {
        str(row["market_ticker"]): {
            "trade_count": int(row.get("trade_count") or 0),
            "latest_trade_at": _iso_or_none(row.get("latest_trade_at")),
            "total_trade_size": float(row.get("total_trade_size") or 0.0),
        }
        for _, row in frame.iterrows()
    }


def _likely_expired_from_row(row: pd.Series) -> bool:
    frame = pd.DataFrame(
        {
            "market_ticker": [row.get("market_ticker")],
            "market_status": [row.get("market_status")],
            "market_close_time": [row.get("market_close_time")],
        }
    )
    return _market_likely_expired(frame)


def _category_for_ticker(ticker: str) -> str:
    upper = ticker.upper()
    if upper.startswith(("KXHIGH", "KXLOW", "KXRAIN", "KXSNOW")):
        return "weather"
    if upper.startswith(("KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXATP", "KXMLB", "KXMLS")):
        return "sports"
    if upper.startswith(("KXPRIMARY", "KXPRES", "KXSENATE", "KXHOUSE", "KXELECTION")):
        return "politics"
    if upper.startswith(("KXBTC", "KXETH", "KXCRYPTO")):
        return "crypto"
    if upper.startswith("KXMVE"):
        return "multivariate"
    return "unknown"


def _event_drift_flags(category: str, likely_expired: bool) -> list[str]:
    flags: list[str] = []
    if category in {"sports", "politics", "crypto", "multivariate", "unknown"}:
        flags.append("event_drift_or_category_review_required")
    if likely_expired:
        flags.append("post_event_review_required")
    return flags


def _unresolved_risks(row: pd.Series, trade: dict[str, Any], likely_expired: bool, category: str) -> list[str]:
    risks = [
        "queue_position_unknown",
        "fees_require_downstream_model",
        "multi_tick_slippage_not_modeled",
    ]
    if int(trade.get("trade_count") or 0) <= 0:
        risks.append("missing_trade_print_evidence")
    if likely_expired:
        risks.append("stale_or_post_event_market")
    if category != "weather":
        risks.append("non_weather_event_fair_value_not_modeled")
    if _num(row.get("spread_cents")) is None:
        risks.append("missing_spread")
    return risks


def _paper_blockers(row: pd.Series, trade: dict[str, Any], likely_expired: bool) -> list[str]:
    blockers: list[str] = []
    if row.get("yes_best_bid") is None or row.get("yes_best_ask") is None:
        blockers.append("missing_real_bid_ask")
    if _int(row.get("depth_snapshot_count")) <= 0:
        blockers.append("missing_displayed_depth")
    if not (_iso_or_none(row.get("latest_snapshot_at")) or _iso_or_none(row.get("ts"))):
        blockers.append("missing_quote_freshness")
    if int(trade.get("trade_count") or 0) <= 0:
        blockers.append("missing_trade_print_evidence")
    if likely_expired:
        blockers.append("stale_post_event_risk")
    blockers.append("paper_candidate_disabled_by_default")
    return sorted(set(blockers))


def _summary(markets: list[dict[str, Any]], start: date | None, end: date | None, full_counts: dict[str, Any]) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    for row in markets:
        category = str(row.get("category") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1
        for blocker in row.get("missing_fields_blocking_paper_market_making") or []:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    return {
        "status": "MARKET_MAKING_SNAPSHOT_OK" if markets else "MARKET_MAKING_SNAPSHOT_NO_LOCAL_DATA",
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat() if end else None,
        "total_markets": int(full_counts.get("total_markets") or len(markets)),
        "markets_with_two_sided_books": int(full_counts.get("markets_with_two_sided_books") or 0),
        "markets_with_bid_ask": int(full_counts.get("markets_with_bid_ask") or 0),
        "markets_with_depth": int(full_counts.get("markets_with_depth") or 0),
        "markets_with_trade_print_evidence": (
            int(full_counts["markets_with_trade_print_evidence"])
            if full_counts.get("markets_with_trade_print_evidence") is not None
            else sum(1 for row in markets if int(row.get("trade_print_evidence_summary", {}).get("trade_count") or 0) > 0)
        ),
        "trade_print_evidence_count_scope": full_counts.get("trade_print_evidence_count_scope") or "full_window",
        "stale_post_event_risk_count": sum(1 for row in markets if row.get("stale_post_event_risk_flags")),
        "event_drift_risk_count": sum(1 for row in markets if row.get("event_drift_risk_flags")),
        "fee_model_missing_count": sum(1 for row in markets if row.get("fee_model_status") != "configured_in_analysis_not_serialized"),
        "quote_freshness_available_count": int(full_counts.get("quote_freshness_available_count") or 0),
        "quote_freshness_missing_count": max(int(full_counts.get("total_markets") or 0) - int(full_counts.get("quote_freshness_available_count") or 0), 0),
        "category_counts": full_counts.get("category_counts") or dict(sorted(category_counts.items())),
        "missing_fields_blocking_paper_market_making_counts": dict(sorted(blocker_counts.items())),
        "paper_candidate_allowed_count": sum(1 for row in markets if row.get("paper_candidate_allowed") is True),
        "research_only": True,
        "execution_enabled": False,
        "readiness_promotion": "none",
        "not_paper_or_live_readiness": True,
    }


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Market-Making Snapshot: Kalshi",
        "",
        "Venue-agnostic market-making snapshot built from local Kalshi orderbook and trade-print tables. It is research-only and does not change readiness gates.",
        "",
        f"- Status: `{summary['status']}`",
        f"- Markets: `{summary['total_markets']}`",
        f"- Two-sided books: `{summary['markets_with_two_sided_books']}`",
        f"- Markets with depth: `{summary['markets_with_depth']}`",
        f"- Markets with trade-print evidence: `{summary['markets_with_trade_print_evidence']}`",
        f"- Trade-print evidence count scope: `{summary['trade_print_evidence_count_scope']}`",
        f"- Stale/post-event risk count: `{summary['stale_post_event_risk_count']}`",
        f"- Event-drift risk count: `{summary['event_drift_risk_count']}`",
        f"- Paper candidate allowed count: `{summary['paper_candidate_allowed_count']}`",
        "",
        "## Category Counts",
        "",
    ]
    for category, count in summary["category_counts"].items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Paper-Making Blockers", ""])
    for blocker, count in summary["missing_fields_blocking_paper_market_making_counts"].items():
        lines.append(f"- `{blocker}`: {count}")
    lines.extend(
        [
            "",
            "## Top Research-Only Examples",
            "",
            "| Ticker | Category | Status | Spread | Trades | Blockers |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload.get("markets", [])[:25]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("ticker") or ""),
                    str(row.get("category") or ""),
                    str(row.get("market_status") or ""),
                    str(row.get("spread_cents") if row.get("spread_cents") is not None else ""),
                    str(row.get("trade_print_evidence_summary", {}).get("trade_count") or 0),
                    ",".join(row.get("missing_fields_blocking_paper_market_making") or []),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "This report never asserts executable liquidity, same-queue priority, live readiness, or paper readiness.",
            "",
        ]
    )
    return "\n".join(lines)


def _export(payload: dict[str, Any], markdown: str) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    json_path = reports / "market_making_snapshot_kalshi.json"
    md_path = reports / "market_making_snapshot_kalshi.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def _none_if_nan(value: Any) -> Any | None:
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return value
    return value
