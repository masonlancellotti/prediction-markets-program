from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from config import PROJECT_ROOT, settings
from data.storage import Storage

QuoteSide = Literal["BUY_YES", "BUY_NO"]


@dataclass(frozen=True)
class MarketMakingConfig:
    min_spread_cents: float = float(settings.passive_min_spread_cents)
    improve_cents: float = 1.0
    quote_size: float = 1.0
    fill_horizon_minutes: int = 30
    quote_spacing_seconds: int = 300
    adverse_selection_penalty_cents: float = float(settings.passive_adverse_selection_penalty_cents)
    min_displayed_depth: float = float(settings.passive_min_displayed_depth)
    max_quotes_per_market_side: int = 300
    weather_only: bool = False


@dataclass(frozen=True)
class MarketMakingResult:
    summary: dict[str, Any]
    markets: list[dict[str, Any]]
    quote_samples: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "markets": self.markets[:100],
            "quote_samples": self.quote_samples[:100],
        }

    def to_text(self) -> str:
        lines = [
            f"market_making_verdict={self.summary.get('market_making_verdict')}",
            f"message={self.summary.get('message')}",
            f"snapshots={self.summary.get('snapshots')} markets={self.summary.get('markets_analyzed')} "
            f"two_sided_markets={self.summary.get('two_sided_markets')} trades={self.summary.get('trades')}",
            f"candidate_markets={self.summary.get('candidate_markets')} filled_markets={self.summary.get('filled_markets')} "
            f"zero_fill_markets={self.summary.get('zero_fill_markets')} "
            f"candidate_quotes={self.summary.get('candidate_quotes')} trade_evidence_fills={self.summary.get('trade_evidence_fills')} "
            f"fill_rate={self.summary.get('trade_evidence_fill_rate'):.3f}",
            f"avg_edge_30m={self.summary.get('avg_future_edge_30m_cents'):.2f} adverse_fill_rate_30m={self.summary.get('adverse_fill_rate_30m'):.3f}",
            f"data_sufficiency={self.summary.get('data_sufficiency')} paper_watchlist_candidates={self.summary.get('paper_watchlist_candidates')}",
            f"weather_only={str(self.summary.get('weather_only')).lower()}",
        ]
        if self.markets:
            readiness_counts: dict[str, int] = {}
            for row in self.markets:
                r = str(row.get("readiness") or "UNKNOWN")
                readiness_counts[r] = readiness_counts.get(r, 0) + 1
            bucket_str = " ".join(f"{k}={v}" for k, v in sorted(readiness_counts.items()))
            lines.append(f"readiness_buckets: {bucket_str}")
        lines.append("Top market-making candidates (fills=trade-print evidence only):")
        for row in self.markets[:10]:
            expired_note = " [LIKELY_EXPIRED]" if row.get("market_likely_expired") else ""
            lines.append(
                f"- {row['market_ticker']}{expired_note} side={row['best_side']} quotes={row['candidate_quotes']} fills={row['trade_evidence_fills']} "
                f"fill_rate={row['fill_rate']:.3f} spread={row['average_candidate_spread_cents']:.2f} "
                f"edge30={row['avg_future_edge_30m_cents']:.2f} edge_net={row['avg_edge_after_penalty_30m_cents']:.2f} "
                f"adverse30={row['adverse_fill_rate_30m']:.3f} score={row['score']:.3f} readiness={row['readiness']}"
            )
        return "\n".join(lines)


class MarketMakingAnalyzer:
    """Offline market-making research over recorded books and observed trades.

    This is deliberately research-only. It treats trade prints as stronger
    passive-fill evidence than orderbook touches, then checks whether those
    hypothetical fills beat future mid prices. It does not place orders.
    """

    def __init__(self, storage: Storage | None = None, config: MarketMakingConfig | None = None):
        self.storage = storage or Storage()
        self.config = config or MarketMakingConfig()

    def analyze(self, start: date | None = None, end: date | None = None, last_days: int | None = None, persist_exports: bool = True) -> MarketMakingResult:
        start, end = _date_window(start, end, last_days)
        book_stats = self._load_book_stats(start, end)
        books = self._load_books(start, end)
        if not int(book_stats.get("snapshots", 0)) or books.empty:
            summary = {
                "market_making_verdict": "NOT_READY_DATA_INCOMPLETE",
                "message": "No live orderbook snapshots in the requested window.",
                "weather_only": self.config.weather_only,
                "snapshots": 0,
                "markets_analyzed": 0,
                "trades": 0,
                "candidate_quotes": 0,
                "trade_evidence_fills": 0,
                "trade_evidence_fill_rate": 0.0,
                "avg_future_edge_30m_cents": 0.0,
                "adverse_fill_rate_30m": 0.0,
                "data_sufficiency": "NEED_ORDERBOOK_DATA",
            }
            return MarketMakingResult(summary, [], [])

        books = _prepare_books(books)
        market_tickers = [str(value) for value in books["market_ticker"].dropna().unique().tolist()]
        trades = _prepare_trades(self._load_trades(start, end, market_tickers=market_tickers))
        trade_groups = {ticker: group.sort_values("ts_dt").reset_index(drop=True) for ticker, group in trades.groupby("market_ticker")} if not trades.empty else {}
        market_rows: list[dict[str, Any]] = []
        samples: list[dict[str, Any]] = []
        for ticker, group in books.groupby("market_ticker"):
            group = group.sort_values("ts_dt").reset_index(drop=True)
            trades_for_market = trade_groups.get(str(ticker), pd.DataFrame())
            metrics, quote_rows = _analyze_market(str(ticker), group, trades_for_market, self.config)
            market_rows.append(metrics)
            samples.extend(quote_rows)

        ranked = sorted(
            market_rows,
            key=lambda row: (
                row["score"],
                row["trade_evidence_fills"],
                row["fill_rate"],
                row["avg_future_edge_30m_cents"],
            ),
            reverse=True,
        )
        samples = sorted(samples, key=lambda row: (row.get("filled") is True, row.get("future_edge_30m_cents") or -999), reverse=True)
        summary = _summary(book_stats, books, trades, ranked, weather_only=self.config.weather_only)
        if persist_exports:
            _export_market_making(ranked, samples, summary)
        return MarketMakingResult(summary, ranked, samples)

    def _load_books(self, start: date | None, end: date | None) -> pd.DataFrame:
        clauses, params = _time_clauses(start, end)
        clauses.extend(["yes_best_bid IS NOT NULL", "yes_best_ask IS NOT NULL", "spread_cents IS NOT NULL"])
        if self.config.weather_only:
            clauses.append(weather_market_filter_clause("market_ticker"))
        return self.storage.fetch_sql(
            f"""
            SELECT market_ticker, ts, yes_best_bid, yes_best_ask, no_best_bid, no_best_ask,
                   spread_cents, mid_cents, depth_yes_bid_1, depth_yes_ask_1,
                   total_yes_bid_depth, total_no_bid_depth, last_price_cents, volume,
                   volume_24h, open_interest, liquidity_cents, market_status, market_close_time
            FROM orderbook_snapshots_live
            WHERE {' AND '.join(clauses)}
            ORDER BY market_ticker, ts
            """,
            params,
        )

    def _load_book_stats(self, start: date | None, end: date | None) -> dict[str, int]:
        clauses, params = _time_clauses(start, end)
        if self.config.weather_only:
            clauses.append(weather_market_filter_clause("market_ticker"))
        frame = self.storage.fetch_sql(
            f"""
            SELECT
                COUNT(*) AS snapshots,
                COUNT(DISTINCT market_ticker) AS markets_analyzed,
                SUM(CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL AND spread_cents IS NOT NULL THEN 1 ELSE 0 END) AS two_sided_snapshots,
                COUNT(DISTINCT CASE WHEN yes_best_bid IS NOT NULL AND yes_best_ask IS NOT NULL AND spread_cents IS NOT NULL THEN market_ticker END) AS two_sided_markets
            FROM orderbook_snapshots_live
            WHERE {' AND '.join(clauses)}
            """,
            params,
        )
        if frame.empty:
            return {"snapshots": 0, "markets_analyzed": 0, "two_sided_snapshots": 0, "two_sided_markets": 0}
        row = frame.iloc[0]
        return {
            "snapshots": _int_value(row.get("snapshots")),
            "markets_analyzed": _int_value(row.get("markets_analyzed")),
            "two_sided_snapshots": _int_value(row.get("two_sided_snapshots")),
            "two_sided_markets": _int_value(row.get("two_sided_markets")),
        }

    def _load_trades(self, start: date | None, end: date | None, market_tickers: list[str] | None = None) -> pd.DataFrame:
        clauses, params = _time_clauses(start, end)
        if market_tickers is None:
            return self.storage.fetch_sql(
                f"""
                SELECT market_ticker, ts, trade_id, price, count, yes_price, no_price, side
                FROM historical_trades
                WHERE {' AND '.join(clauses)}
                ORDER BY market_ticker, ts
                """,
                params,
            )
        if not market_tickers:
            return pd.DataFrame(columns=["market_ticker", "ts", "trade_id", "price", "count", "yes_price", "no_price", "side"])
        frames: list[pd.DataFrame] = []
        unique_tickers = list(dict.fromkeys(market_tickers))
        for start_idx in range(0, len(unique_tickers), 400):
            chunk = unique_tickers[start_idx : start_idx + 400]
            chunk_params = dict(params)
            placeholders = []
            for idx, ticker in enumerate(chunk):
                key = f"ticker_{start_idx}_{idx}"
                placeholders.append(f":{key}")
                chunk_params[key] = ticker
            chunk_clauses = [*clauses, f"market_ticker IN ({', '.join(placeholders)})"]
            frame = self.storage.fetch_sql(
                f"""
                SELECT market_ticker, ts, trade_id, price, count, yes_price, no_price, side
                FROM historical_trades
                WHERE {' AND '.join(chunk_clauses)}
                ORDER BY market_ticker, ts
                """,
                chunk_params,
            )
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=["market_ticker", "ts", "trade_id", "price", "count", "yes_price", "no_price", "side"])
        return pd.concat(frames, ignore_index=True)


def _analyze_market(ticker: str, books: pd.DataFrame, trades: pd.DataFrame, cfg: MarketMakingConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    quote_rows: list[dict[str, Any]] = []
    side_metrics: list[dict[str, Any]] = []
    for side in ("BUY_YES", "BUY_NO"):
        side_quotes = _candidate_quotes(ticker, side, books, cfg)
        evaluated = [_evaluate_quote(quote, books, trades, cfg) for quote in side_quotes]
        side_metrics.append(_side_metrics(ticker, side, books, trades, evaluated))
        quote_rows.extend(evaluated[:50])

    best = max(side_metrics, key=lambda row: (row["score"], row["trade_evidence_fills"], row["avg_future_edge_30m_cents"]))
    combined = _combined_market_metrics(ticker, books, trades, side_metrics, best)
    return combined, quote_rows


def _candidate_quotes(ticker: str, side: QuoteSide, books: pd.DataFrame, cfg: MarketMakingConfig) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    last_ts: pd.Timestamp | None = None
    for _, row in books.iterrows():
        ts = row.get("ts_dt")
        if pd.isna(ts):
            continue
        if last_ts is not None and (ts - last_ts).total_seconds() < cfg.quote_spacing_seconds:
            continue
        spread = _num(row.get("spread_cents"))
        if spread is None or spread < cfg.min_spread_cents:
            continue
        if side == "BUY_YES":
            bid = _num(row.get("yes_best_bid"))
            ask = _num(row.get("yes_best_ask"))
            depth = _num(row.get("depth_yes_bid_1")) or 0.0
        else:
            bid = _num(row.get("no_best_bid"))
            ask = _num(row.get("no_best_ask"))
            depth = _num(row.get("depth_yes_ask_1")) or 0.0
        if bid is None or ask is None:
            continue
        limit_price = min(ask - 1.0, bid + cfg.improve_cents)
        if limit_price <= 0 or limit_price >= ask or depth < cfg.min_displayed_depth:
            continue
        quotes.append(
            {
                "market_ticker": ticker,
                "side": side,
                "quote_ts": ts,
                "limit_price": float(limit_price),
                "opposing_ask": float(ask),
                "same_side_bid": float(bid),
                "spread_cents": float(spread),
                "displayed_depth": float(depth),
                "maker_spread_to_ask_cents": float(ask - limit_price),
            }
        )
        last_ts = ts
        if len(quotes) >= cfg.max_quotes_per_market_side:
            break
    return quotes


def _evaluate_quote(quote: dict[str, Any], books: pd.DataFrame, trades: pd.DataFrame, cfg: MarketMakingConfig) -> dict[str, Any]:
    quote_ts = quote["quote_ts"]
    horizon_ts = quote_ts + pd.Timedelta(minutes=cfg.fill_horizon_minutes)
    future_books = books[(books["ts_dt"] > quote_ts) & (books["ts_dt"] <= horizon_ts)]
    future_trades = trades[(trades["ts_dt"] > quote_ts) & (trades["ts_dt"] <= horizon_ts)] if not trades.empty else trades
    fill = _trade_fill_for_quote(quote["side"], quote["limit_price"], future_trades)
    touched = _book_touch_for_quote(quote["side"], quote["limit_price"], future_books)
    row = {
        **quote,
        "quote_ts": quote_ts.to_pydatetime(),
        "filled": fill is not None,
        "fill_ts": fill.get("fill_ts").to_pydatetime() if fill else None,
        "fill_price": float(quote["limit_price"]) if fill else None,
        "fill_trade_price": fill.get("trade_price") if fill else None,
        "fill_trade_id": fill.get("trade_id") if fill else None,
        "touched_without_trade": fill is None and touched,
        "touch_ts": touched.to_pydatetime() if fill is None and touched is not None else None,
    }
    if fill is None:
        row.update({f"future_edge_{minutes}m_cents": None for minutes in (5, 15, 30, 60)})
        row["edge_after_penalty_30m_cents"] = None
        return row
    for minutes in (5, 15, 30, 60):
        future_mid = _future_side_mid_after(books, fill["fill_ts"], minutes, quote["side"])
        row[f"future_edge_{minutes}m_cents"] = None if future_mid is None else float(future_mid - quote["limit_price"])
    edge_30 = row.get("future_edge_30m_cents")
    row["edge_after_penalty_30m_cents"] = None if edge_30 is None else float(edge_30 - cfg.adverse_selection_penalty_cents)
    return row


def _trade_fill_for_quote(side: QuoteSide, limit_price: float, future_trades: pd.DataFrame) -> dict[str, Any] | None:
    if future_trades.empty:
        return None
    frame = future_trades.copy()
    if side == "BUY_YES":
        frame["trade_price_for_side"] = pd.to_numeric(frame.get("yes_price", frame.get("price")), errors="coerce")
        hits = frame[frame["trade_price_for_side"] <= limit_price]
    else:
        no_price = pd.to_numeric(frame.get("no_price", pd.Series(dtype=float)), errors="coerce")
        yes_price = pd.to_numeric(frame.get("yes_price", frame.get("price")), errors="coerce")
        frame["trade_price_for_side"] = no_price.where(no_price.notna(), 100.0 - yes_price)
        hits = frame[frame["trade_price_for_side"] <= limit_price]
    if hits.empty:
        return None
    hit = hits.sort_values("ts_dt").iloc[0]
    return {
        "fill_ts": hit["ts_dt"],
        "trade_price": _num(hit.get("trade_price_for_side")),
        "trade_id": None if pd.isna(hit.get("trade_id")) else str(hit.get("trade_id")),
    }


def _book_touch_for_quote(side: QuoteSide, limit_price: float, future_books: pd.DataFrame) -> pd.Timestamp | None:
    if future_books.empty:
        return None
    ask_col = "yes_best_ask" if side == "BUY_YES" else "no_best_ask"
    asks = pd.to_numeric(future_books.get(ask_col, pd.Series(dtype=float)), errors="coerce")
    touched = future_books[asks <= limit_price]
    if touched.empty:
        return None
    return touched.iloc[0]["ts_dt"]


def _side_metrics(ticker: str, side: QuoteSide, books: pd.DataFrame, trades: pd.DataFrame, rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [row for row in rows if row["filled"]]
    touched = [row for row in rows if row["touched_without_trade"]]
    edge_30 = [_num(row.get("future_edge_30m_cents")) for row in filled]
    edge_30 = [value for value in edge_30 if value is not None]
    edge_after_penalty = [_num(row.get("edge_after_penalty_30m_cents")) for row in filled]
    edge_after_penalty = [value for value in edge_after_penalty if value is not None]
    adverse = [value for value in edge_30 if value < 0]
    fill_rate = len(filled) / len(rows) if rows else 0.0
    avg_edge_30 = sum(edge_30) / len(edge_30) if edge_30 else 0.0
    avg_after_penalty = sum(edge_after_penalty) / len(edge_after_penalty) if edge_after_penalty else 0.0
    adverse_rate = len(adverse) / len(edge_30) if edge_30 else 0.0
    score = _market_making_score(len(filled), fill_rate, avg_after_penalty, adverse_rate)
    spread = pd.to_numeric(books.get("spread_cents", pd.Series(dtype=float)), errors="coerce").dropna()
    return {
        "market_ticker": ticker,
        "side": side,
        "snapshots": int(len(books)),
        "trades": int(len(trades)),
        "candidate_quotes": int(len(rows)),
        "trade_evidence_fills": int(len(filled)),
        "touches_without_trade": int(len(touched)),
        "fill_rate": float(fill_rate),
        "average_spread_cents": float(spread.mean()) if not spread.empty else 0.0,
        "average_candidate_spread_cents": _avg(row.get("spread_cents") for row in rows),
        "median_spread_cents": float(spread.median()) if not spread.empty else 0.0,
        "p90_spread_cents": float(spread.quantile(0.9)) if not spread.empty else 0.0,
        "avg_maker_spread_to_ask_cents": _avg(row.get("maker_spread_to_ask_cents") for row in rows),
        "avg_future_edge_5m_cents": _avg(row.get("future_edge_5m_cents") for row in filled),
        "avg_future_edge_15m_cents": _avg(row.get("future_edge_15m_cents") for row in filled),
        "avg_future_edge_30m_cents": float(avg_edge_30),
        "avg_future_edge_60m_cents": _avg(row.get("future_edge_60m_cents") for row in filled),
        "avg_edge_after_penalty_30m_cents": float(avg_after_penalty),
        "adverse_fill_rate_30m": float(adverse_rate),
        "score": float(score),
    }


def _combined_market_metrics(ticker: str, books: pd.DataFrame, trades: pd.DataFrame, sides: list[dict[str, Any]], best: dict[str, Any]) -> dict[str, Any]:
    likely_expired = _market_likely_expired(books)
    return {
        "market_ticker": ticker,
        "best_side": best["side"],
        "snapshots": int(len(books)),
        "trades": int(len(trades)),
        "candidate_quotes": int(sum(row["candidate_quotes"] for row in sides)),
        "trade_evidence_fills": int(sum(row["trade_evidence_fills"] for row in sides)),
        "touches_without_trade": int(sum(row["touches_without_trade"] for row in sides)),
        "fill_rate": float(sum(row["trade_evidence_fills"] for row in sides) / max(sum(row["candidate_quotes"] for row in sides), 1)),
        "average_spread_cents": best["average_spread_cents"],
        "average_candidate_spread_cents": best["average_candidate_spread_cents"],
        "median_spread_cents": best["median_spread_cents"],
        "p90_spread_cents": best["p90_spread_cents"],
        "avg_maker_spread_to_ask_cents": best["avg_maker_spread_to_ask_cents"],
        "avg_future_edge_5m_cents": best["avg_future_edge_5m_cents"],
        "avg_future_edge_15m_cents": best["avg_future_edge_15m_cents"],
        "avg_future_edge_30m_cents": best["avg_future_edge_30m_cents"],
        "avg_future_edge_60m_cents": best["avg_future_edge_60m_cents"],
        "avg_edge_after_penalty_30m_cents": best["avg_edge_after_penalty_30m_cents"],
        "adverse_fill_rate_30m": best["adverse_fill_rate_30m"],
        "score": best["score"],
        "market_likely_expired": likely_expired,
        "yes_side_json": json.dumps(next(row for row in sides if row["side"] == "BUY_YES"), default=str),
        "no_side_json": json.dumps(next(row for row in sides if row["side"] == "BUY_NO"), default=str),
        "readiness": _market_readiness(best),
    }


def _summary(book_stats: dict[str, int], books: pd.DataFrame, trades: pd.DataFrame, markets: list[dict[str, Any]], weather_only: bool = False) -> dict[str, Any]:
    candidate_quotes = sum(row["candidate_quotes"] for row in markets)
    fills = sum(row["trade_evidence_fills"] for row in markets)
    candidate_markets = sum(1 for row in markets if row["candidate_quotes"] > 0)
    filled_markets = sum(1 for row in markets if row["trade_evidence_fills"] > 0)
    zero_fill_markets = sum(1 for row in markets if row["candidate_quotes"] > 0 and row["trade_evidence_fills"] == 0)
    weighted_edge_num = sum(row["avg_future_edge_30m_cents"] * row["trade_evidence_fills"] for row in markets if row["trade_evidence_fills"] > 0)
    weighted_adverse_num = sum(row["adverse_fill_rate_30m"] * row["trade_evidence_fills"] for row in markets if row["trade_evidence_fills"] > 0)
    strong = [row for row in markets if row["readiness"] == "PAPER_WATCHLIST"]
    watchlist_tickers = [
        {
            "market_ticker": row["market_ticker"],
            "best_side": row["best_side"],
            "trade_evidence_fills": int(row["trade_evidence_fills"]),
            "avg_edge_after_penalty_30m_cents": float(row["avg_edge_after_penalty_30m_cents"]),
            "average_spread_cents": float(row["average_spread_cents"]),
            "score": float(row["score"]),
            "market_likely_expired": bool(row.get("market_likely_expired", False)),
        }
        for row in sorted(strong, key=lambda x: x["score"], reverse=True)
    ]
    one_sided_or_empty_snapshots = max(0, int(book_stats.get("snapshots", 0)) - int(book_stats.get("two_sided_snapshots", 0)))
    if len(books) < 10000 or len(trades) < 100:
        sufficiency = "NEED_MORE_COLLECTION"
    elif fills < 30 or filled_markets < 5:
        sufficiency = "ENOUGH_TO_MONITOR_NOT_ENOUGH_FILLS"
    else:
        sufficiency = "ENOUGH_FOR_RESEARCH_REVIEW"
    if strong:
        verdict = "PAPER_WATCHLIST_CANDIDATES"
        message = "Some markets have trade-evidence fills with positive post-fill future-mid edge; review CSVs before any paper quoting."
    elif fills >= 30:
        verdict = "RESEARCH_READY_NO_PAPER_EDGE_YET"
        message = "There is enough fill evidence to study, but no robust paper-watchlist candidate yet."
    else:
        verdict = "COLLECT_MORE_TRADE_EVIDENCE"
        message = "Orderbook data is useful, but passive fill evidence is still thin."
    return {
        "market_making_verdict": verdict,
        "message": message,
        "weather_only": bool(weather_only),
        "data_sufficiency": sufficiency,
        "snapshots": int(book_stats.get("snapshots", 0)),
        "markets_analyzed": int(book_stats.get("markets_analyzed", 0)),
        "two_sided_snapshots": int(book_stats.get("two_sided_snapshots", 0)),
        "two_sided_markets": int(book_stats.get("two_sided_markets", 0)),
        "one_sided_or_empty_snapshots": one_sided_or_empty_snapshots,
        "trades": int(len(trades)),
        "trade_markets": int(trades["market_ticker"].nunique()) if not trades.empty else 0,
        "candidate_markets": int(candidate_markets),
        "candidate_quotes": int(candidate_quotes),
        "trade_evidence_fills": int(fills),
        "filled_markets": int(filled_markets),
        "zero_fill_markets": int(zero_fill_markets),
        "trade_evidence_fill_rate": float(fills / max(candidate_quotes, 1)),
        "avg_future_edge_30m_cents": float(weighted_edge_num / fills) if fills else 0.0,
        "adverse_fill_rate_30m": float(weighted_adverse_num / fills) if fills else 0.0,
        "paper_watchlist_candidates": len(strong),
        "paper_watchlist_tickers": watchlist_tickers,
    }


def _market_likely_expired(books: pd.DataFrame) -> bool:
    """Return True if the latest known market status indicates the market is no longer active."""
    if "market_status" not in books.columns:
        return False
    status_col = books["market_status"].dropna()
    if status_col.empty:
        return False
    latest = str(status_col.iloc[-1]).strip().lower()
    return latest in {"finalized", "settled", "closed", "resolved"}


def _market_making_score(fills: int, fill_rate: float, edge_after_penalty: float, adverse_rate: float) -> float:
    return max(0.0, edge_after_penalty) * min(fills, 50) / 50.0 * max(0.0, 1.0 - adverse_rate) * min(fill_rate * 10.0, 1.0)


def _market_readiness(best: dict[str, Any]) -> str:
    fills = best["trade_evidence_fills"]
    if fills == 0:
        return "ZERO_TRADE_PRINT_FILLS"
    if fills < 10:
        return "FEW_FILLS_NEED_MORE"
    if best["avg_edge_after_penalty_30m_cents"] <= 0:
        return "ADVERSE_SELECTION_OR_NO_EDGE"
    if best["adverse_fill_rate_30m"] > 0.45:
        return "TOO_MUCH_ADVERSE_SELECTION"
    if fills >= 30:
        return "PAPER_WATCHLIST"
    return "PROMISING_NEEDS_MORE_FILLS"


def _future_side_mid_after(books: pd.DataFrame, fill_ts: pd.Timestamp, minutes: int, side: QuoteSide) -> float | None:
    target = fill_ts + pd.Timedelta(minutes=minutes)
    future = books[books["ts_dt"] >= target]
    if future.empty:
        return None
    row = future.iloc[0]
    yes_mid = _num(row.get("mid_cents"))
    if yes_mid is None:
        bid = _num(row.get("yes_best_bid"))
        ask = _num(row.get("yes_best_ask"))
        yes_mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
    if yes_mid is None:
        return None
    return float(yes_mid if side == "BUY_YES" else 100.0 - yes_mid)


def _prepare_books(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    prepared["ts_dt"] = pd.to_datetime(prepared["ts"], errors="coerce", utc=True)
    for col in ["yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask", "spread_cents", "mid_cents"]:
        if col in prepared:
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
    return prepared.dropna(subset=["ts_dt"])


def _two_sided_books(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[
        frame["yes_best_bid"].notna()
        & frame["yes_best_ask"].notna()
        & frame["spread_cents"].notna()
    ].copy()


def _prepare_trades(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    prepared = frame.copy()
    prepared["ts_dt"] = pd.to_datetime(prepared["ts"], errors="coerce", utc=True)
    for col in ["price", "count", "yes_price", "no_price"]:
        if col in prepared:
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
    return prepared.dropna(subset=["ts_dt"])


def _export_market_making(markets: list[dict[str, Any]], samples: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    pd.DataFrame(markets).to_csv(reports / "market_making_candidates.csv", index=False)
    pd.DataFrame(samples).to_csv(reports / "market_making_quote_samples.csv", index=False)
    (reports / "market_making_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


def _date_window(start: date | None, end: date | None, last_days: int | None) -> tuple[date | None, date | None]:
    if last_days is None:
        return start, end
    end_date = end or date.today()
    return end_date - timedelta(days=max(last_days, 1)), end_date


def _time_clauses(start: date | None, end: date | None) -> tuple[list[str], dict[str, Any]]:
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if start:
        clauses.append("ts >= :start_ts")
        params["start_ts"] = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if end:
        clauses.append("ts < :end_ts")
        params["end_ts"] = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return clauses, params


def _avg(values) -> float:
    nums = [_num(value) for value in values]
    nums = [value for value in nums if value is not None]
    return float(sum(nums) / len(nums)) if nums else 0.0


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    numeric = _num(value)
    return int(numeric) if numeric is not None else 0


def weather_market_filter_clause(column: str = "market_ticker") -> str:
    """Return a SQL fragment that keeps only tickers parsed as weather contracts."""
    return f"{column} IN (SELECT DISTINCT market_ticker FROM parsed_contracts WHERE market_ticker IS NOT NULL)"
