from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy.exc import OperationalError

from backtest.execution import NormalizedOrderBook
from config import PROJECT_ROOT, settings
from data.kalshi_client import KalshiClient
from data.kalshi_market_loader import KalshiMarketLoader
from data.storage import Storage
from live.orderbook_recorder import extract_multi_orderbooks, normalize_live_orderbook_snapshot

LOGGER = logging.getLogger(__name__)


PRIORITY_HIGH = "RECORD_HIGH_PRIORITY"
PRIORITY_MEDIUM = "RECORD_MEDIUM_PRIORITY"
PRIORITY_LOW = "RECORD_LOW_PRIORITY"
PRIORITY_METADATA = "METADATA_ONLY"
PRIORITY_IGNORE = "IGNORE_EMPTY_OR_DEAD"


@dataclass(frozen=True)
class MarketUniverseConfig:
    min_spread_cents: float = float(settings.passive_min_spread_cents)
    min_displayed_depth: float = float(settings.passive_min_displayed_depth)
    recent_hours: int = 24
    batch_size: int = 100
    excluded_ticker_prefixes: tuple[str, ...] = ("KXMVE",)


@dataclass(frozen=True)
class MarketUniverseResult:
    summary: dict[str, Any]
    rows: list[dict[str, Any]]

    def to_text(self) -> str:
        priority_counts = self.summary.get("priority_counts", {})
        lines = [
            f"market_universe_verdict={self.summary.get('verdict')}",
            f"message={self.summary.get('message')}",
            f"markets_discovered={self.summary.get('markets_discovered')} selected_for_probe={self.summary.get('markets_selected_for_probe')} "
            f"probed_books={self.summary.get('probed_books')} "
            f"two_sided={self.summary.get('two_sided_markets')} candidates={self.summary.get('candidate_markets')} "
            f"recent_trade_markets={self.summary.get('recent_trade_markets')} excluded_prefix={self.summary.get('excluded_by_prefix')}",
            "priority_counts="
            + ", ".join(f"{key}:{priority_counts.get(key, 0)}" for key in [PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW, PRIORITY_METADATA, PRIORITY_IGNORE]),
            f"exports={self.summary.get('exports')}",
        ]
        lines.append("Top universe rows:")
        for row in self.rows[:15]:
            lines.append(
                f"- {row['market_ticker']} priority={row['priority']} score={row['score']:.1f} "
                f"spread={_fmt(row.get('spread_cents'))} depth={_fmt(row.get('min_depth'))} "
                f"trades={row.get('recent_trade_count')} volume24h={_fmt(row.get('volume_24h'))} reason={row.get('reason')}"
            )
        return "\n".join(lines)


class MarketUniverseBuilder:
    """Discover open Kalshi markets and rank which ones deserve recorder budget."""

    def __init__(
        self,
        client: KalshiClient | None = None,
        storage: Storage | None = None,
        config: MarketUniverseConfig | None = None,
    ):
        self.client = client or KalshiClient()
        self.storage = storage or Storage()
        self.config = config or MarketUniverseConfig()
        self.loader = KalshiMarketLoader(client=self.client, storage=self.storage)

    def build(
        self,
        *,
        max_pages: int | None = 5,
        max_markets: int | None = None,
        probe_limit: int | None = 1000,
        probe_orderbooks: bool = True,
        use_local_stats: bool = True,
        persist_markets: bool = False,
        persist: bool = True,
        export: bool = True,
    ) -> MarketUniverseResult:
        if use_local_stats or persist or persist_markets:
            try:
                self.storage.init_db()
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                LOGGER.warning("rank-market-universe could not run schema initialization because SQLite is locked; continuing with existing schema")
        ranked_at = datetime.now(timezone.utc)
        run_id = ranked_at.strftime("%Y%m%dT%H%M%SZ")
        markets = self.loader.load_active_markets(
            persist=persist_markets,
            persist_snapshots=False,
            max_pages=max_pages,
            max_markets=max_markets,
        )
        LOGGER.info("rank-market-universe discovered markets=%d", len(markets))
        market_by_ticker = {str(market.get("ticker") or ""): market for market in markets if market.get("ticker")}
        tickers = list(market_by_ticker)
        recent_stats = self._recent_stats(self.config.recent_hours) if use_local_stats else {}
        probe_candidates = [
            ticker for ticker in tickers if not _excluded_prefix(ticker, self.config.excluded_ticker_prefixes)
        ]
        probe_tickers = _probe_tickers(probe_candidates, market_by_ticker, recent_stats, probe_limit)
        LOGGER.info("rank-market-universe selected probe_tickers=%d", len(probe_tickers))
        books = self._probe_orderbooks(probe_tickers) if probe_orderbooks else {}
        rows: list[dict[str, Any]] = []
        for ticker in tickers:
            row = score_market_universe_row(
                market_by_ticker[ticker],
                books.get(ticker),
                recent_stats.get(ticker, {}),
                self.config,
                ranked_at=ranked_at,
                run_id=run_id,
            )
            rows.append(row)
        rows.sort(key=lambda row: (row["score"], row["recent_trade_count"], row["volume_24h"] or 0.0), reverse=True)
        if persist:
            LOGGER.info("rank-market-universe persisting ranked rows=%d", len(rows))
            self.storage.upsert_market_universe_rankings(rows)
        exports = _export_universe(rows, _summary(rows, markets, books, len(probe_tickers)), export)
        summary = _summary(rows, markets, books, len(probe_tickers))
        summary["exports"] = exports
        return MarketUniverseResult(summary=summary, rows=rows)

    def _probe_orderbooks(self, tickers: list[str]) -> dict[str, dict]:
        books: dict[str, dict] = {}
        chunks = _chunks(tickers, self.config.batch_size)
        for idx, chunk in enumerate(chunks, start=1):
            if idx == 1 or idx % 10 == 0 or idx == len(chunks):
                LOGGER.info("rank-market-universe probing orderbook batch %d/%d", idx, len(chunks))
            payload = self.client.get_multiple_orderbooks(chunk)
            for ticker, raw in extract_multi_orderbooks(payload, chunk):
                books[ticker] = raw
        return books

    def _recent_stats(self, recent_hours: int) -> dict[str, dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=max(recent_hours, 1))).strftime("%Y-%m-%d %H:%M:%S")
        orderbooks = self.storage.fetch_sql(
            """
            SELECT market_ticker,
                   COUNT(*) AS recent_snapshot_count,
                   SUM(CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL AND spread_cents IS NOT NULL THEN 1 ELSE 0 END) AS recent_two_sided_count,
                   SUM(CASE WHEN spread_cents >= :min_spread
                             AND yes_best_bid IS NOT NULL
                             AND yes_best_ask IS NOT NULL
                             AND (COALESCE(depth_yes_bid_1, 0) >= :min_depth OR COALESCE(depth_yes_ask_1, 0) >= :min_depth)
                            THEN 1 ELSE 0 END) AS recent_candidate_count
            FROM orderbook_snapshots_live
            WHERE ts >= :since
            GROUP BY market_ticker
            """,
            {"since": since, "min_spread": self.config.min_spread_cents, "min_depth": self.config.min_displayed_depth},
        )
        trades = self.storage.fetch_sql(
            """
            SELECT market_ticker, COUNT(*) AS recent_trade_count
            FROM historical_trades
            WHERE ts >= :since
            GROUP BY market_ticker
            """,
            {"since": since},
        )
        stats: dict[str, dict[str, Any]] = {}
        for _, row in orderbooks.iterrows():
            ticker = str(row.get("market_ticker") or "")
            stats[ticker] = {
                "recent_snapshot_count": _int(row.get("recent_snapshot_count")),
                "recent_two_sided_count": _int(row.get("recent_two_sided_count")),
                "recent_candidate_count": _int(row.get("recent_candidate_count")),
                "recent_trade_count": 0,
            }
        for _, row in trades.iterrows():
            ticker = str(row.get("market_ticker") or "")
            bucket = stats.setdefault(
                ticker,
                {
                    "recent_snapshot_count": 0,
                    "recent_two_sided_count": 0,
                    "recent_candidate_count": 0,
                    "recent_trade_count": 0,
                },
            )
            bucket["recent_trade_count"] = _int(row.get("recent_trade_count"))
        return stats


def score_market_universe_row(
    market: dict[str, Any],
    raw_book: dict | None,
    recent_stats: dict[str, Any],
    cfg: MarketUniverseConfig,
    *,
    ranked_at: datetime | None = None,
    run_id: str = "test",
) -> dict[str, Any]:
    ranked_at = ranked_at or datetime.now(timezone.utc)
    ticker = str(market.get("ticker") or "")
    ticker_family = _ticker_family(ticker)
    excluded_by_prefix = _excluded_prefix(ticker, cfg.excluded_ticker_prefixes)
    book_row = _book_features(ticker, raw_book, market)
    recent_snapshot_count = _int(recent_stats.get("recent_snapshot_count"))
    recent_two_sided_count = _int(recent_stats.get("recent_two_sided_count"))
    recent_candidate_count = _int(recent_stats.get("recent_candidate_count"))
    recent_trade_count = _int(recent_stats.get("recent_trade_count"))
    volume_24h = _market_volume(market.get("volume_24h_fp"), market.get("volume_24h"))
    open_interest = _market_volume(market.get("open_interest_fp"), market.get("open_interest"))
    liquidity_cents = _market_price_cents(market.get("liquidity_dollars"), market.get("liquidity"))
    has_two_sided = bool(book_row.get("has_two_sided_book"))
    has_candidate = bool(
        has_two_sided
        and (book_row.get("spread_cents") or 0.0) >= cfg.min_spread_cents
        and (book_row.get("min_depth") or 0.0) >= cfg.min_displayed_depth
    )
    score, reasons = _score_components(
        has_two_sided=has_two_sided,
        has_candidate=has_candidate,
        spread_cents=book_row.get("spread_cents"),
        min_depth=book_row.get("min_depth"),
        volume_24h=volume_24h,
        open_interest=open_interest,
        liquidity_cents=liquidity_cents,
        recent_snapshot_count=recent_snapshot_count,
        recent_candidate_count=recent_candidate_count,
        recent_trade_count=recent_trade_count,
    )
    if excluded_by_prefix:
        priority = PRIORITY_IGNORE
        reasons.insert(0, f"excluded ticker prefix {ticker_family}; multivariate/combinatoric books are usually empty or not useful for this maker recorder")
        has_candidate = False
    else:
        priority = _priority(score, has_two_sided, has_candidate, recent_candidate_count, recent_trade_count, volume_24h, liquidity_cents)
    payload = {
        "market": market,
        "book": raw_book,
        "recent_stats": recent_stats,
        "score_reasons": reasons,
    }
    return {
        "market_ticker": ticker,
        "run_id": run_id,
        "ranked_at": ranked_at,
        "priority": priority,
        "score": float(score),
        "category": str(market.get("category") or ""),
        "series_ticker": str(market.get("series_ticker") or ""),
        "event_ticker": str(market.get("event_ticker") or ""),
        "ticker_family": ticker_family,
        "excluded_by_prefix": int(excluded_by_prefix),
        "status": str(market.get("status") or ""),
        "close_time": _parse_market_dt(market.get("close_time")),
        "has_two_sided_book": int(has_two_sided),
        "has_candidate_book": int(has_candidate),
        "spread_cents": book_row.get("spread_cents"),
        "mid_cents": book_row.get("mid_cents"),
        "yes_best_bid": book_row.get("yes_best_bid"),
        "yes_best_ask": book_row.get("yes_best_ask"),
        "depth_yes_bid_1": book_row.get("depth_yes_bid_1"),
        "depth_yes_ask_1": book_row.get("depth_yes_ask_1"),
        "min_depth": book_row.get("min_depth"),
        "total_depth": book_row.get("total_depth"),
        "volume_24h": volume_24h,
        "open_interest": open_interest,
        "liquidity_cents": liquidity_cents,
        "recent_snapshot_count": recent_snapshot_count,
        "recent_two_sided_count": recent_two_sided_count,
        "recent_candidate_count": recent_candidate_count,
        "recent_trade_count": recent_trade_count,
        "reason": "; ".join(reasons[:6]),
        "raw_json": json.dumps(payload, default=str),
    }


def _book_features(ticker: str, raw_book: dict | None, market: dict[str, Any]) -> dict[str, Any]:
    if not raw_book:
        return {"has_two_sided_book": 0}
    try:
        book = NormalizedOrderBook.from_kalshi(ticker, raw_book)
        row = normalize_live_orderbook_snapshot(ticker, datetime.now(timezone.utc), book, raw_book, market_payload=market)
    except Exception:
        return {"has_two_sided_book": 0}
    yes_bid = row.get("yes_best_bid")
    yes_ask = row.get("yes_best_ask")
    has_two_sided = yes_bid is not None and yes_ask is not None
    min_depth = min(float(row.get("depth_yes_bid_1") or 0.0), float(row.get("depth_yes_ask_1") or 0.0))
    return {
        "has_two_sided_book": int(has_two_sided),
        "spread_cents": row.get("spread_cents"),
        "mid_cents": row.get("mid_cents"),
        "yes_best_bid": yes_bid,
        "yes_best_ask": yes_ask,
        "depth_yes_bid_1": row.get("depth_yes_bid_1"),
        "depth_yes_ask_1": row.get("depth_yes_ask_1"),
        "min_depth": min_depth,
        "total_depth": float(row.get("total_yes_bid_depth") or 0.0) + float(row.get("total_no_bid_depth") or 0.0),
    }


def _score_components(
    *,
    has_two_sided: bool,
    has_candidate: bool,
    spread_cents: float | None,
    min_depth: float | None,
    volume_24h: float | None,
    open_interest: float | None,
    liquidity_cents: float | None,
    recent_snapshot_count: int,
    recent_candidate_count: int,
    recent_trade_count: int,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if has_candidate:
        score += 35.0
        reasons.append("current book is two-sided with enough spread/depth")
    elif has_two_sided:
        score += 15.0
        reasons.append("current book is two-sided")
    else:
        reasons.append("current book empty or one-sided")
    if spread_cents is not None:
        spread_score = min(max(float(spread_cents), 0.0), 30.0) * 0.4
        score += spread_score
        if spread_cents >= settings.passive_min_spread_cents:
            reasons.append(f"spread {spread_cents:.1f}c")
    if min_depth:
        score += min(math.log1p(max(float(min_depth), 0.0)) * 4.0, 15.0)
        reasons.append(f"min depth {min_depth:.1f}")
    if recent_trade_count:
        score += min(math.log1p(recent_trade_count) * 8.0, 25.0)
        reasons.append(f"{recent_trade_count} recent trades")
    if recent_candidate_count:
        score += min(math.log1p(recent_candidate_count) * 6.0, 20.0)
        reasons.append(f"{recent_candidate_count} recent candidate snapshots")
    if recent_snapshot_count:
        score += min(math.log1p(recent_snapshot_count) * 2.0, 8.0)
    if volume_24h:
        score += min(math.log1p(max(float(volume_24h), 0.0)) * 3.0, 15.0)
        reasons.append(f"24h volume {volume_24h:.0f}")
    if open_interest:
        score += min(math.log1p(max(float(open_interest), 0.0)) * 2.0, 10.0)
    if liquidity_cents:
        score += min(math.log1p(max(float(liquidity_cents), 0.0)) * 2.0, 10.0)
    return score, reasons


def _priority(
    score: float,
    has_two_sided: bool,
    has_candidate: bool,
    recent_candidate_count: int,
    recent_trade_count: int,
    volume_24h: float | None,
    liquidity_cents: float | None,
) -> str:
    has_activity = recent_trade_count > 0 or (volume_24h or 0.0) > 0.0 or (liquidity_cents or 0.0) > 0.0
    if has_candidate and has_activity and score >= 55:
        return PRIORITY_HIGH
    if (has_candidate or recent_candidate_count > 0) and score >= 35:
        return PRIORITY_MEDIUM
    if has_two_sided or has_activity:
        return PRIORITY_LOW
    if volume_24h or liquidity_cents:
        return PRIORITY_METADATA
    return PRIORITY_IGNORE


def _probe_tickers(tickers: list[str], market_by_ticker: dict[str, dict], recent_stats: dict[str, dict[str, Any]], probe_limit: int | None) -> list[str]:
    ranked = sorted(
        tickers,
        key=lambda ticker: _probe_score(market_by_ticker.get(ticker, {}), recent_stats.get(ticker, {})),
        reverse=True,
    )
    return ranked[:probe_limit] if probe_limit else ranked


def _probe_score(market: dict[str, Any], stats: dict[str, Any]) -> float:
    volume_24h = _market_volume(market.get("volume_24h_fp"), market.get("volume_24h")) or 0.0
    open_interest = _market_volume(market.get("open_interest_fp"), market.get("open_interest")) or 0.0
    liquidity = _market_price_cents(market.get("liquidity_dollars"), market.get("liquidity")) or 0.0
    recent_trades = _int(stats.get("recent_trade_count"))
    recent_candidates = _int(stats.get("recent_candidate_count"))
    recent_two_sided = _int(stats.get("recent_two_sided_count"))
    return (
        math.log1p(max(volume_24h, 0.0)) * 10.0
        + math.log1p(max(open_interest, 0.0)) * 5.0
        + math.log1p(max(liquidity, 0.0)) * 4.0
        + math.log1p(recent_trades) * 20.0
        + math.log1p(recent_candidates) * 15.0
        + math.log1p(recent_two_sided) * 5.0
    )


def _summary(rows: list[dict[str, Any]], markets: list[dict], books: dict[str, dict], probe_ticker_count: int) -> dict[str, Any]:
    priority_counts = {priority: 0 for priority in [PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW, PRIORITY_METADATA, PRIORITY_IGNORE]}
    for row in rows:
        priority_counts[row["priority"]] = priority_counts.get(row["priority"], 0) + 1
    candidate_markets = sum(1 for row in rows if row["has_candidate_book"])
    two_sided_markets = sum(1 for row in rows if row["has_two_sided_book"])
    recent_trade_markets = sum(1 for row in rows if row["recent_trade_count"] > 0)
    excluded_by_prefix = sum(1 for row in rows if row.get("excluded_by_prefix"))
    high = priority_counts.get(PRIORITY_HIGH, 0)
    medium = priority_counts.get(PRIORITY_MEDIUM, 0)
    verdict = "UNIVERSE_READY_FOR_PRIORITY_RECORDING" if high or medium else "UNIVERSE_DISCOVERED_NO_STRONG_PRIORITY"
    message = "Use high/medium priority markets for recorder budget." if high or medium else "Most discovered markets look empty, one-sided, or inactive."
    return {
        "verdict": verdict,
        "message": message,
        "markets_discovered": len(markets),
        "markets_selected_for_probe": probe_ticker_count,
        "probed_books": len(books),
        "two_sided_markets": two_sided_markets,
        "candidate_markets": candidate_markets,
        "recent_trade_markets": recent_trade_markets,
        "excluded_by_prefix": excluded_by_prefix,
        "priority_counts": priority_counts,
    }


def _export_universe(rows: list[dict[str, Any]], summary: dict[str, Any], export: bool) -> dict[str, str] | None:
    if not export:
        return None
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    csv_path = reports / "market_universe_ranked.csv"
    json_path = reports / "market_universe_summary.json"
    txt_path = reports / "market_universe_high_priority_tickers.txt"
    recordable_path = reports / "market_universe_recordable_tickers.txt"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    tickers = [
        str(row["market_ticker"])
        for row in rows
        if row["priority"] in {PRIORITY_HIGH, PRIORITY_MEDIUM}
    ]
    txt_path.write_text("\n".join(tickers), encoding="utf-8")
    recordable = [
        str(row["market_ticker"])
        for row in rows
        if row["priority"] in {PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW}
    ]
    recordable_path.write_text("\n".join(recordable), encoding="utf-8")
    return {
        "csv": str(csv_path),
        "summary": str(json_path),
        "priority_tickers": str(txt_path),
        "recordable_tickers": str(recordable_path),
    }


def _ticker_family(ticker: str) -> str:
    if "-" in ticker:
        return ticker.split("-", 1)[0]
    return ticker[:12] if ticker else ""


def _excluded_prefix(ticker: str, prefixes: tuple[str, ...]) -> bool:
    normalized = ticker.upper()
    return any(normalized.startswith(prefix.upper()) for prefix in prefixes if prefix)


def _market_price_cents(dollars: object, cents: object) -> float | None:
    for candidate in (dollars, cents):
        if candidate in (None, ""):
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 1:
            return value * 100.0
        return value
    return None


def _market_volume(volume_fp: object, volume: object) -> float | None:
    for candidate in (volume_fp, volume):
        if candidate in (None, ""):
            continue
        try:
            return float(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _parse_market_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[start : start + size] for start in range(0, len(items), max(size, 1))]


def _int(value: object) -> int:
    try:
        if value is None or value != value:
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _fmt(value: Any) -> str:
    try:
        if value is None or value != value:
            return "none"
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "none"
