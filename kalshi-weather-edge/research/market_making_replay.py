from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import pandas as pd

from backtest.fees import ConservativeFixedFeeModel
from config import PROJECT_ROOT, settings
from data.storage import Storage
from research.market_making_analysis import weather_market_filter_clause

ReplaySide = Literal["BUY_YES", "BUY_NO"]


@dataclass(frozen=True)
class MarketMakingReplayConfig:
    side: ReplaySide | None = None
    quantity: float = 1.0
    max_position: float = 5.0
    max_open_quotes: int = 1
    improve_cents: float = 1.0
    min_spread_cents: float = float(settings.passive_min_spread_cents)
    min_depth: float = float(settings.passive_min_displayed_depth)
    quote_ttl_seconds: int = 300
    quote_spacing_seconds: int = 300
    stale_current_seconds: int = 180
    require_current_setup: bool = False
    weather_only: bool = False
    adverse_selection_penalty_cents: float = float(settings.passive_adverse_selection_penalty_cents)
    max_quotes_per_market_side: int = 500


@dataclass(frozen=True)
class MarketMakingReplayResult:
    summary: dict[str, Any]
    markets: list[dict[str, Any]]
    fills: list[dict[str, Any]]

    def to_text(self) -> str:
        lines = [
            f"market_making_backtest_verdict={self.summary.get('verdict')}",
            f"message={self.summary.get('message')}",
            f"snapshots={self.summary.get('snapshots')} markets={self.summary.get('markets_analyzed')} trades={self.summary.get('trades')}",
            f"quotes_opened={self.summary.get('quotes_opened')} fills={self.summary.get('fills')} cancels={self.summary.get('cancels')} "
            f"fill_rate={self.summary.get('fill_rate'):.3f}",
            f"avg_edge_30m={self.summary.get('avg_future_edge_30m_cents'):.2f} "
            f"avg_net_edge_30m={self.summary.get('avg_net_edge_30m_cents'):.2f} "
            f"adverse30={self.summary.get('adverse_fill_rate_30m'):.3f}",
            f"weather_only={str(self.summary.get('weather_only')).lower()}",
            f"paper_test_candidates={self.summary.get('paper_test_candidates')} "
            f"current_paper_targets={self.summary.get('current_paper_targets')} "
            f"replay_supported_current_targets={self.summary.get('replay_supported_current_targets')} exports={self.summary.get('exports')}",
        ]
        next_command = self.summary.get("next_paper_command")
        if next_command:
            lines.append(f"next_paper_command={next_command}")
        lines.append("Top replay candidates:")
        for row in self.markets[:10]:
            lines.append(
                f"- {row['market_ticker']} side={row['best_side']} quotes={row['quotes_opened']} "
                f"fills={row['fills']} fill_rate={row['fill_rate']:.3f} "
                f"net30={row['avg_net_edge_30m_cents']:.2f} adverse30={row['adverse_fill_rate_30m']:.3f} "
                f"current_ok={row.get('current_setup_ok')} current_spread={_fmt(row.get('current_spread_cents'))} "
                f"readiness={row['readiness']} reason={row.get('current_setup_reason')}"
            )
        return "\n".join(lines)


class MarketMakingReplayBacktester:
    """Conservative replay of the paper market-maker loop over recorded data.

    This is a screening tool, not proof of live profitability. It only fills
    passive quotes when actual trade prints trade through the simulated limit
    price before the quote TTL.
    """

    def __init__(self, storage: Storage | None = None, config: MarketMakingReplayConfig | None = None):
        self.storage = storage or Storage()
        self.config = config or MarketMakingReplayConfig()
        self.fee_model = ConservativeFixedFeeModel()

    def replay(
        self,
        start: date | None = None,
        end: date | None = None,
        last_days: int | None = None,
        market_ticker: str | None = None,
        max_markets: int | None = None,
        persist_exports: bool = True,
    ) -> MarketMakingReplayResult:
        start, end = _date_window(start, end, last_days)
        selected_tickers = None
        if market_ticker is None and max_markets is not None and max_markets > 0:
            selected_tickers = self._select_market_tickers(start, end, max_markets)
            if not selected_tickers:
                summary = _empty_summary("NOT_READY_DATA_INCOMPLETE", "No recorded orderbook markets in the requested window.")
                summary["weather_only"] = self.config.weather_only
                return MarketMakingReplayResult(summary=summary, markets=[], fills=[])
        books = self._load_books(start, end, market_ticker, selected_tickers)
        trades = self._load_trades(start, end, market_ticker, selected_tickers)
        if books.empty:
            summary = _empty_summary("NOT_READY_DATA_INCOMPLETE", "No recorded orderbook snapshots in the requested window.")
            summary["weather_only"] = self.config.weather_only
            return MarketMakingReplayResult(summary=summary, markets=[], fills=[])

        books = _prepare_books(books)
        trades = _prepare_trades(trades)
        trade_groups = {ticker: group.sort_values("ts_dt").reset_index(drop=True) for ticker, group in trades.groupby("market_ticker")} if not trades.empty else {}
        market_rows: list[dict[str, Any]] = []
        fill_rows: list[dict[str, Any]] = []
        for ticker, group in books.groupby("market_ticker"):
            group = group.sort_values("ts_dt").reset_index(drop=True)
            trades_for_market = trade_groups.get(str(ticker), pd.DataFrame())
            metrics, fills = self._replay_market(str(ticker), group, trades_for_market)
            market_rows.append(metrics)
            fill_rows.extend(fills)

        ranked = sorted(
            market_rows,
            key=lambda row: (row["score"], row["fills"], row["avg_net_edge_30m_cents"], row["fill_rate"]),
            reverse=True,
        )
        ranked = self._attach_current_setup(ranked)
        if self.config.require_current_setup:
            ranked = [row for row in ranked if row.get("current_setup_ok")]
            kept_tickers = {row["market_ticker"] for row in ranked}
            fill_rows = [row for row in fill_rows if row.get("market_ticker") in kept_tickers]
        fill_rows = sorted(fill_rows, key=lambda row: row.get("net_edge_30m_cents") if row.get("net_edge_30m_cents") is not None else -999, reverse=True)
        summary = _summary(books, trades, ranked, fill_rows, weather_only=self.config.weather_only)
        if persist_exports:
            summary["exports"] = _export(ranked, fill_rows, summary)
        else:
            summary["exports"] = None
        return MarketMakingReplayResult(summary=summary, markets=ranked, fills=fill_rows)

    def _attach_current_setup(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return rows
        latest = self._latest_books([row["market_ticker"] for row in rows])
        now = datetime.now(timezone.utc)
        enriched: list[dict[str, Any]] = []
        for row in rows:
            current_book = latest.get(row["market_ticker"])
            setup = self._current_setup(row["best_side"], current_book, now)
            enriched.append({**row, **setup})
        return enriched

    def _latest_books(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        if not tickers:
            return {}
        unique = list(dict.fromkeys(tickers))
        latest: dict[str, dict[str, Any]] = {}
        for start in range(0, len(unique), 300):
            chunk = unique[start : start + 300]
            params: dict[str, Any] = {}
            placeholders = []
            for idx, ticker in enumerate(chunk):
                key = f"ticker_{start}_{idx}"
                placeholders.append(f":{key}")
                params[key] = ticker
            frame = self.storage.fetch_sql(
                f"""
                SELECT obs.*
                FROM orderbook_snapshots_live obs
                JOIN (
                    SELECT market_ticker, MAX(ts) AS max_ts
                    FROM orderbook_snapshots_live
                    WHERE market_ticker IN ({', '.join(placeholders)})
                    GROUP BY market_ticker
                ) latest
                  ON obs.market_ticker = latest.market_ticker
                 AND obs.ts = latest.max_ts
                """,
                params,
            )
            for _, item in frame.iterrows():
                latest[str(item["market_ticker"])] = item.to_dict()
        return latest

    def _current_setup(self, side: ReplaySide, book: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
        base = {
            "current_setup_ok": False,
            "current_setup_reason": "No latest orderbook snapshot.",
            "current_book_ts": None,
            "current_book_age_seconds": None,
            "current_status": None,
            "current_spread_cents": None,
            "current_mid_cents": None,
            "current_same_side_bid_cents": None,
            "current_opposing_ask_cents": None,
            "current_limit_price_cents": None,
            "current_displayed_depth": None,
        }
        if not book:
            return base
        book_ts = _parse_ts(book.get("ts"))
        age = None if book_ts is None else (now - book_ts).total_seconds()
        spread = _num(book.get("spread_cents"))
        base.update(
            {
                "current_book_ts": book_ts,
                "current_book_age_seconds": age,
                "current_status": book.get("market_status"),
                "current_spread_cents": spread,
                "current_mid_cents": _num(book.get("mid_cents")),
            }
        )
        if book_ts is None:
            base["current_setup_reason"] = "Latest orderbook timestamp is invalid."
            return base
        if age is not None and age > self.config.stale_current_seconds:
            base["current_setup_reason"] = f"Latest orderbook is stale: {age:.0f}s old."
            return base
        status = _clean_text(book.get("market_status"))
        if status and status not in {"open", "active"}:
            base["current_setup_reason"] = f"Market status is {status}."
            return base
        if spread is None or spread < self.config.min_spread_cents:
            base["current_setup_reason"] = f"Spread {spread} below minimum {self.config.min_spread_cents:.2f}c."
            return base
        if side == "BUY_YES":
            bid = _num(book.get("yes_best_bid"))
            ask = _num(book.get("yes_best_ask"))
            depth = _num(book.get("depth_yes_bid_1")) or 0.0
        else:
            bid = _num(book.get("no_best_bid"))
            ask = _num(book.get("no_best_ask"))
            depth = _num(book.get("depth_no_bid_1"))
            if depth is None:
                depth = _num(book.get("depth_yes_ask_1")) or 0.0
        base.update(
            {
                "current_same_side_bid_cents": bid,
                "current_opposing_ask_cents": ask,
                "current_displayed_depth": depth,
            }
        )
        if bid is None or ask is None:
            base["current_setup_reason"] = "Current book is missing same-side bid or opposing ask."
            return base
        if depth < self.config.min_depth:
            base["current_setup_reason"] = f"Displayed depth {depth:.2f} below minimum {self.config.min_depth:.2f}."
            return base
        limit_price = min(ask - 1.0, bid + self.config.improve_cents)
        base["current_limit_price_cents"] = float(limit_price)
        if limit_price <= 0 or limit_price >= ask:
            base["current_setup_reason"] = "Improved quote would cross/touch the opposing ask."
            return base
        base["current_setup_ok"] = True
        base["current_setup_reason"] = f"Current book qualifies for a {side} paper quote at {limit_price:.2f}c."
        return base

    def _select_market_tickers(self, start: date | None, end: date | None, max_markets: int) -> list[str]:
        clauses, params = _time_clauses(start, end)
        clauses.extend(["yes_best_bid IS NOT NULL", "yes_best_ask IS NOT NULL", "spread_cents IS NOT NULL"])
        if self.config.weather_only:
            clauses.append(weather_market_filter_clause("market_ticker"))
        frame = self.storage.fetch_sql(
            f"""
            SELECT market_ticker, COUNT(*) AS snapshots
            FROM orderbook_snapshots_live
            WHERE {' AND '.join(clauses)}
            GROUP BY market_ticker
            ORDER BY snapshots DESC
            LIMIT :max_markets
            """,
            {**params, "max_markets": max_markets},
        )
        if frame.empty:
            return []
        return [str(value) for value in frame["market_ticker"].dropna().tolist()]

    def _load_books(self, start: date | None, end: date | None, market_ticker: str | None, selected_tickers: list[str] | None) -> pd.DataFrame:
        clauses, params = _time_clauses(start, end)
        clauses.extend(["yes_best_bid IS NOT NULL", "yes_best_ask IS NOT NULL", "spread_cents IS NOT NULL"])
        if market_ticker:
            clauses.append("market_ticker = :market_ticker")
            params["market_ticker"] = market_ticker
        elif selected_tickers:
            placeholders = []
            for idx, ticker in enumerate(selected_tickers):
                key = f"ticker_{idx}"
                placeholders.append(f":{key}")
                params[key] = ticker
            clauses.append(f"market_ticker IN ({', '.join(placeholders)})")
        if self.config.weather_only:
            clauses.append(weather_market_filter_clause("market_ticker"))
        return self.storage.fetch_sql(
            f"""
            SELECT market_ticker, ts, yes_best_bid, yes_best_ask, no_best_bid, no_best_ask,
                   spread_cents, mid_cents, depth_yes_bid_1, depth_yes_ask_1,
                   depth_no_bid_1, depth_no_ask_1, market_status
            FROM orderbook_snapshots_live
            WHERE {' AND '.join(clauses)}
            ORDER BY market_ticker, ts
            """,
            params,
        )

    def _load_trades(self, start: date | None, end: date | None, market_ticker: str | None, selected_tickers: list[str] | None) -> pd.DataFrame:
        clauses, params = _time_clauses(start, end)
        if market_ticker:
            clauses.append("market_ticker = :market_ticker")
            params["market_ticker"] = market_ticker
        elif selected_tickers:
            placeholders = []
            for idx, ticker in enumerate(selected_tickers):
                key = f"ticker_{idx}"
                placeholders.append(f":{key}")
                params[key] = ticker
            clauses.append(f"market_ticker IN ({', '.join(placeholders)})")
        return self.storage.fetch_sql(
            f"""
            SELECT market_ticker, ts, trade_id, price, count, yes_price, no_price, side
            FROM historical_trades
            WHERE {' AND '.join(clauses)}
            ORDER BY market_ticker, ts
            """,
            params,
        )

    def _replay_market(self, ticker: str, books: pd.DataFrame, trades: pd.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        side_rows: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        sides: list[ReplaySide] = [self.config.side] if self.config.side else ["BUY_YES", "BUY_NO"]
        for side in sides:
            metrics, side_fills = self._replay_market_side(ticker, side, books, trades)
            side_rows.append(metrics)
            fills.extend(side_fills)
        best = max(side_rows, key=lambda row: (row["score"], row["fills"], row["avg_net_edge_30m_cents"]))
        combined = {
            "market_ticker": ticker,
            "best_side": best["side"],
            "snapshots": int(len(books)),
            "trades": int(len(trades)),
            "quotes_opened": int(sum(row["quotes_opened"] for row in side_rows)),
            "fills": int(sum(row["fills"] for row in side_rows)),
            "cancels": int(sum(row["cancels"] for row in side_rows)),
            "fill_rate": float(sum(row["fills"] for row in side_rows) / max(sum(row["quotes_opened"] for row in side_rows), 1)),
            "avg_future_edge_30m_cents": best["avg_future_edge_30m_cents"],
            "avg_net_edge_30m_cents": best["avg_net_edge_30m_cents"],
            "adverse_fill_rate_30m": best["adverse_fill_rate_30m"],
            "score": best["score"],
            "readiness": _readiness(best),
            "side_json": json.dumps(side_rows, default=str),
        }
        return combined, fills

    def _replay_market_side(self, ticker: str, side: ReplaySide, books: pd.DataFrame, trades: pd.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        open_quotes: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        quotes_opened = 0
        cancels = 0
        inventory = 0.0
        last_quote_ts: pd.Timestamp | None = None
        for _, book in books.iterrows():
            ts = book.get("ts_dt")
            if pd.isna(ts):
                continue
            fill_rows, cancelled = self._refresh_open_quotes(ticker, side, open_quotes, trades, books, ts)
            fills.extend(fill_rows)
            cancels += cancelled
            inventory += sum(float(row["quantity"]) for row in fill_rows)
            open_quantity = sum(float(row["quantity"]) for row in open_quotes)
            if quotes_opened >= self.config.max_quotes_per_market_side:
                continue
            if last_quote_ts is not None and (ts - last_quote_ts).total_seconds() < self.config.quote_spacing_seconds:
                continue
            if len(open_quotes) >= self.config.max_open_quotes:
                continue
            if inventory + open_quantity + self.config.quantity > self.config.max_position:
                continue
            candidate = self._candidate_quote(side, book)
            if not candidate:
                continue
            open_quotes.append(
                {
                    **candidate,
                    "market_ticker": ticker,
                    "side": side,
                    "quote_ts": ts,
                    "expires_at": ts + pd.Timedelta(seconds=self.config.quote_ttl_seconds),
                    "quantity": self.config.quantity,
                }
            )
            quotes_opened += 1
            last_quote_ts = ts

        if not books.empty:
            last_ts = books["ts_dt"].iloc[-1] + pd.Timedelta(seconds=self.config.quote_ttl_seconds)
            fill_rows, cancelled = self._refresh_open_quotes(ticker, side, open_quotes, trades, books, last_ts)
            fills.extend(fill_rows)
            cancels += cancelled
        metrics = _side_metrics(ticker, side, books, trades, quotes_opened, cancels, fills)
        return metrics, fills

    def _refresh_open_quotes(
        self,
        ticker: str,
        side: ReplaySide,
        open_quotes: list[dict[str, Any]],
        trades: pd.DataFrame,
        books: pd.DataFrame,
        current_ts: pd.Timestamp,
    ) -> tuple[list[dict[str, Any]], int]:
        fills: list[dict[str, Any]] = []
        cancels = 0
        remaining: list[dict[str, Any]] = []
        for quote in open_quotes:
            search_end = min(current_ts, quote["expires_at"])
            fill = _trade_fill_for_quote(side, quote["limit_price_cents"], trades, quote["quote_ts"], search_end)
            if fill:
                fee = self.fee_model.fee_cents(int(round(quote["limit_price_cents"])), quote["quantity"])
                future_edges = _future_edges(books, fill["fill_ts"], side, quote["limit_price_cents"], quote["quantity"], fee)
                fills.append(
                    {
                        "market_ticker": ticker,
                        "side": side,
                        "quote_ts": quote["quote_ts"].to_pydatetime(),
                        "fill_ts": fill["fill_ts"].to_pydatetime(),
                        "limit_price_cents": quote["limit_price_cents"],
                        "fill_trade_price_cents": fill["trade_price"],
                        "fill_trade_id": fill["trade_id"],
                        "quantity": quote["quantity"],
                        "fee_cents": fee,
                        "spread_cents": quote["spread_cents"],
                        "displayed_depth": quote["displayed_depth"],
                        **future_edges,
                    }
                )
                continue
            if current_ts >= quote["expires_at"]:
                cancels += 1
                continue
            remaining.append(quote)
        open_quotes[:] = remaining
        return fills, cancels

    def _candidate_quote(self, side: ReplaySide, book: pd.Series) -> dict[str, Any] | None:
        status = _clean_text(book.get("market_status"))
        if status and status not in {"open", "active"}:
            return None
        spread = _num(book.get("spread_cents"))
        if spread is None or spread < self.config.min_spread_cents:
            return None
        if side == "BUY_YES":
            bid = _num(book.get("yes_best_bid"))
            ask = _num(book.get("yes_best_ask"))
            depth = _num(book.get("depth_yes_bid_1")) or 0.0
        else:
            bid = _num(book.get("no_best_bid"))
            ask = _num(book.get("no_best_ask"))
            depth = _num(book.get("depth_no_bid_1"))
            if depth is None:
                depth = _num(book.get("depth_yes_ask_1")) or 0.0
        if bid is None or ask is None or depth < self.config.min_depth:
            return None
        limit_price = min(ask - 1.0, bid + self.config.improve_cents)
        if limit_price <= 0 or limit_price >= ask:
            return None
        return {
            "limit_price_cents": float(limit_price),
            "same_side_bid_cents": float(bid),
            "opposing_ask_cents": float(ask),
            "spread_cents": float(spread),
            "displayed_depth": float(depth),
        }


def _side_metrics(
    ticker: str,
    side: ReplaySide,
    books: pd.DataFrame,
    trades: pd.DataFrame,
    quotes_opened: int,
    cancels: int,
    fills: list[dict[str, Any]],
) -> dict[str, Any]:
    edge_30 = [_num(row.get("future_edge_30m_cents")) for row in fills]
    edge_30 = [value for value in edge_30 if value is not None]
    net_30 = [_num(row.get("net_edge_30m_cents")) for row in fills]
    net_30 = [value for value in net_30 if value is not None]
    adverse = [value for value in edge_30 if value < 0]
    fill_rate = len(fills) / max(quotes_opened, 1)
    avg_net = sum(net_30) / len(net_30) if net_30 else 0.0
    adverse_rate = len(adverse) / len(edge_30) if edge_30 else 0.0
    score = max(0.0, avg_net) * min(len(net_30), 50) / 50.0 * max(0.0, 1.0 - adverse_rate) * min(fill_rate * 10.0, 1.0)
    return {
        "market_ticker": ticker,
        "side": side,
        "snapshots": int(len(books)),
        "trades": int(len(trades)),
        "quotes_opened": int(quotes_opened),
        "fills": int(len(fills)),
        "cancels": int(cancels),
        "fill_rate": float(fill_rate),
        "avg_future_edge_30m_cents": float(sum(edge_30) / len(edge_30)) if edge_30 else 0.0,
        "avg_net_edge_30m_cents": float(avg_net),
        "adverse_fill_rate_30m": float(adverse_rate),
        "score": float(score),
    }


def _summary(books: pd.DataFrame, trades: pd.DataFrame, markets: list[dict[str, Any]], fills: list[dict[str, Any]], weather_only: bool = False) -> dict[str, Any]:
    quotes_opened = sum(row["quotes_opened"] for row in markets)
    fill_count = sum(row["fills"] for row in markets)
    cancels = sum(row["cancels"] for row in markets)
    edge_30 = [_num(row.get("future_edge_30m_cents")) for row in fills]
    edge_30 = [value for value in edge_30 if value is not None]
    net_30 = [_num(row.get("net_edge_30m_cents")) for row in fills]
    net_30 = [value for value in net_30 if value is not None]
    adverse = [value for value in edge_30 if value < 0]
    paper_candidates = [row for row in markets if row["readiness"] == "PAPER_TEST_CANDIDATE"]
    current_targets = [row for row in markets if row.get("current_setup_ok")]
    supported_current_targets = [
        row
        for row in current_targets
        if row.get("fills", 0) > 0
        and row.get("avg_net_edge_30m_cents", 0.0) > 0
        and row.get("adverse_fill_rate_30m", 1.0) <= 0.35
    ]
    if paper_candidates:
        verdict = "PAPER_TEST_CANDIDATES"
        message = "Replay found trade-print-filled passive setups worth forward paper testing."
    elif fill_count >= 20:
        verdict = "REPLAY_NO_PAPER_EDGE_YET"
        message = "Replay has fill evidence, but no robust paper-test candidate yet."
    else:
        verdict = "COLLECT_MORE_TRADE_EVIDENCE"
        message = "Replay ran, but trade-print fills are still too thin."
    return {
        "verdict": verdict,
        "message": message,
        "weather_only": bool(weather_only),
        "snapshots": int(len(books)),
        "markets_analyzed": int(books["market_ticker"].nunique()) if not books.empty else 0,
        "trades": int(len(trades)),
        "trade_markets": int(trades["market_ticker"].nunique()) if not trades.empty else 0,
        "quotes_opened": int(quotes_opened),
        "fills": int(fill_count),
        "cancels": int(cancels),
        "fill_rate": float(fill_count / max(quotes_opened, 1)),
        "avg_future_edge_30m_cents": float(sum(edge_30) / len(edge_30)) if edge_30 else 0.0,
        "avg_net_edge_30m_cents": float(sum(net_30) / len(net_30)) if net_30 else 0.0,
        "adverse_fill_rate_30m": float(len(adverse) / len(edge_30)) if edge_30 else 0.0,
        "paper_test_candidates": int(len(paper_candidates)),
        "current_paper_targets": int(len(current_targets)),
        "replay_supported_current_targets": int(len(supported_current_targets)),
        "next_paper_command": _next_paper_command(supported_current_targets),
    }


def _readiness(row: dict[str, Any]) -> str:
    if row["fills"] < 5:
        return "NEED_MORE_FILLS"
    if row["avg_net_edge_30m_cents"] <= 0:
        return "NO_NET_EDGE_AFTER_FEES"
    if row["adverse_fill_rate_30m"] > 0.35:
        return "TOO_MUCH_ADVERSE_SELECTION"
    if row["fills"] >= 10:
        return "PAPER_TEST_CANDIDATE"
    return "PROMISING_NEEDS_MORE_FILLS"


def _empty_summary(verdict: str, message: str) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "message": message,
        "weather_only": False,
        "snapshots": 0,
        "markets_analyzed": 0,
        "trades": 0,
        "quotes_opened": 0,
        "fills": 0,
        "cancels": 0,
        "fill_rate": 0.0,
        "avg_future_edge_30m_cents": 0.0,
        "avg_net_edge_30m_cents": 0.0,
        "adverse_fill_rate_30m": 0.0,
        "paper_test_candidates": 0,
        "current_paper_targets": 0,
        "replay_supported_current_targets": 0,
        "next_paper_command": None,
        "exports": None,
    }


def _trade_fill_for_quote(side: ReplaySide, limit_price: float, trades: pd.DataFrame, quote_ts: pd.Timestamp, not_after: pd.Timestamp) -> dict[str, Any] | None:
    if trades.empty:
        return None
    frame = trades[(trades["ts_dt"] > quote_ts) & (trades["ts_dt"] <= not_after)].copy()
    if frame.empty:
        return None
    if side == "BUY_YES":
        frame["trade_price_for_side"] = pd.to_numeric(frame.get("yes_price", frame.get("price")), errors="coerce")
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


def _future_edges(books: pd.DataFrame, fill_ts: pd.Timestamp, side: ReplaySide, fill_price: float, quantity: float, fee: float) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for minutes in (5, 15, 30, 60):
        mark = _future_side_mid_after(books, fill_ts, minutes, side)
        edge = None if mark is None else float(mark - fill_price)
        values[f"future_edge_{minutes}m_cents"] = edge
        values[f"net_edge_{minutes}m_cents"] = None if edge is None else float(edge * quantity - fee)
    return values


def _future_side_mid_after(books: pd.DataFrame, fill_ts: pd.Timestamp, minutes: int, side: ReplaySide) -> float | None:
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
    for col in [
        "yes_best_bid",
        "yes_best_ask",
        "no_best_bid",
        "no_best_ask",
        "spread_cents",
        "mid_cents",
        "depth_yes_bid_1",
        "depth_yes_ask_1",
        "depth_no_bid_1",
        "depth_no_ask_1",
    ]:
        if col in prepared:
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
    return prepared.dropna(subset=["ts_dt"])


def _prepare_trades(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    prepared = frame.copy()
    prepared["ts_dt"] = pd.to_datetime(prepared["ts"], errors="coerce", utc=True)
    for col in ["price", "count", "yes_price", "no_price"]:
        if col in prepared:
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
    return prepared.dropna(subset=["ts_dt"])


def _export(markets: list[dict[str, Any]], fills: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    candidates_path = reports / "market_making_replay_candidates.csv"
    fills_path = reports / "market_making_replay_fills.csv"
    summary_path = reports / "market_making_replay_summary.json"
    pd.DataFrame(markets).to_csv(candidates_path, index=False)
    pd.DataFrame(fills).to_csv(fills_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return {"candidates": str(candidates_path), "fills": str(fills_path), "summary": str(summary_path)}


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


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _next_paper_command(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    row = rows[0]
    return (
        "python main.py paper-market-making "
        f"--market-ticker {row['market_ticker']} --side {row['best_side']} "
        "--interval-seconds 30 --duration-minutes 60 --quantity 1 "
        "--max-position 5 --max-open-quotes 1"
    )


def _fmt(value: Any) -> str:
    number = _num(value)
    return "none" if number is None else f"{number:.2f}"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except TypeError:
        pass
    return str(value).strip().lower()
