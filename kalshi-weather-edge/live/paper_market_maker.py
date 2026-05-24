from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import pandas as pd

from backtest.fees import ConservativeFixedFeeModel
from config import PROJECT_ROOT, settings
from data.storage import Storage
from research.edge_types import LIVE_PAPER, PASSIVE_LIQUIDITY_SPREAD_EDGE

PaperMakerSide = Literal["BUY_YES", "BUY_NO"]

STRATEGY_NAME = "paper_market_maker"
STRATEGY_VERSION = "paper_market_maker_v1_trade_print_fills"


@dataclass(frozen=True)
class PaperMarketMakerConfig:
    market_ticker: str
    side: PaperMakerSide
    quantity: float = 1.0
    max_position: float = 5.0
    max_open_quotes: int = 1
    improve_cents: float = 1.0
    min_spread_cents: float = float(settings.passive_min_spread_cents)
    min_depth: float = float(settings.passive_min_displayed_depth)
    quote_ttl_seconds: int = 300
    stale_orderbook_seconds: int = 180
    interval_seconds: int = 30
    duration_minutes: float | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class PaperMarketMakerResult:
    summary: dict[str, Any]
    actions: list[dict[str, Any]]

    def to_text(self) -> str:
        lines = [
            f"paper_market_making_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"market={self.summary.get('market_ticker')} side={self.summary.get('side')} "
            f"open_quotes={self.summary.get('open_quotes')} filled_quotes={self.summary.get('filled_quotes')} "
            f"cancelled_quotes={self.summary.get('cancelled_quotes')}",
            f"last_action={self.summary.get('last_action')} last_reason={self.summary.get('last_reason')}",
            f"inventory={self.summary.get('inventory_quantity'):.2f} avg_fill={_fmt(self.summary.get('avg_fill_price_cents'))} "
            f"current_mark={_fmt(self.summary.get('current_mark_cents'))} "
            f"unrealized_pnl={_fmt(self.summary.get('unrealized_pnl_cents'))}",
            f"fill_rate={self.summary.get('fill_rate'):.3f} avg_edge_30m={_fmt(self.summary.get('avg_future_edge_30m_cents'))} "
            f"future30_n={self.summary.get('future_edge_30m_observations')} adverse30={self.summary.get('adverse_fill_rate_30m'):.3f}",
            f"exports={self.summary.get('exports')}",
        ]
        lines.append("Actions:")
        for action in self.actions[:20]:
            lines.append(f"- {action.get('action')} {action.get('reason')}")
        return "\n".join(lines)


class PaperMarketMaker:
    """Paper-only passive market maker for one market/side.

    The command never calls order endpoints. It reads locally recorded books,
    logs hypothetical passive quotes, marks fills only when actual trade prints
    trade through the quote price, and reports paper inventory/P&L.
    """

    def __init__(self, storage: Storage | None = None, now_fn: Callable[[], datetime] | None = None):
        self.storage = storage or Storage()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.fee_model = ConservativeFixedFeeModel()

    def run(self, config: PaperMarketMakerConfig, *, persist_exports: bool = True, once: bool = False) -> PaperMarketMakerResult:
        started = self.now_fn()
        deadline = None
        if config.duration_minutes and config.duration_minutes > 0:
            deadline = started + timedelta(minutes=float(config.duration_minutes))
        last_result: PaperMarketMakerResult | None = None
        while True:
            last_result = self.run_once(config, persist_exports=persist_exports)
            if once or deadline is None or self.now_fn() >= deadline:
                return last_result
            _print_progress(last_result)
            time.sleep(max(1, int(config.interval_seconds)))

    def run_once(self, config: PaperMarketMakerConfig, *, persist_exports: bool = True) -> PaperMarketMakerResult:
        self.storage.init_db()
        now = self.now_fn()
        actions: list[dict[str, Any]] = []
        self._refresh_open_quotes(config, now, actions)
        self._refresh_filled_quotes(config)
        latest_book = self._latest_book(config.market_ticker)
        summary_before = self._summary(config, latest_book)
        if config.dry_run:
            actions.append({"action": "DRY_RUN", "reason": "Dry run requested; no paper quote inserted."})
        else:
            maybe_quote = self._candidate_quote(config, latest_book, now, summary_before)
            if maybe_quote["ok"]:
                quote_id = self._insert_quote(config, maybe_quote["quote"], now)
                actions.append({"action": "QUOTE_OPENED", "reason": f"Opened paper quote id={quote_id} at {maybe_quote['quote']['limit_price_cents']:.2f}c."})
            else:
                actions.append({"action": "NO_QUOTE", "reason": maybe_quote["reason"]})
        summary = self._summary(config, latest_book)
        if actions:
            summary["last_action"] = actions[-1].get("action")
            summary["last_reason"] = actions[-1].get("reason")
        summary["status"] = _summary_status(summary)
        summary["message"] = _summary_message(summary)
        if persist_exports:
            summary["exports"] = self._export(config, summary)
        else:
            summary["exports"] = None
        return PaperMarketMakerResult(summary=summary, actions=actions)

    def _refresh_open_quotes(self, config: PaperMarketMakerConfig, now: datetime, actions: list[dict[str, Any]]) -> None:
        open_quotes = self._open_quotes(config.market_ticker, config.side)
        for _, quote in open_quotes.iterrows():
            quote_time = _parse_ts(quote.get("quote_time"))
            if quote_time is None:
                continue
            expires_at = quote_time + timedelta(seconds=config.quote_ttl_seconds)
            fill_search_end = min(now, expires_at)
            fill = self._fill_for_quote(config, quote, quote_time, fill_search_end)
            quote_id = int(quote["id"])
            if fill:
                limit_price = float(quote["limit_price_cents"])
                quantity = float(quote.get("quantity") or config.quantity)
                fee = self.fee_model.fee_cents(int(round(limit_price)), quantity)
                values = {
                    "status": "FILLED",
                    "fill_time": fill["fill_time"],
                    "fill_price_cents": limit_price,
                    "fill_trade_price_cents": fill["trade_price"],
                    "fill_trade_id": fill["trade_id"],
                    "fee_cents": fee,
                }
                values.update(self._future_edges(config.market_ticker, config.side, fill["fill_time"], limit_price))
                mark = self._current_mark(config.market_ticker, config.side)
                values["current_mark_cents"] = mark
                values["unrealized_pnl_cents"] = _marked_pnl(config.side, limit_price, quantity, mark, fee)
                self.storage.update_paper_market_making_quote(quote_id, values)
                self._insert_paper_order_event(config, quote_id, "filled_paper", limit_price, values, quote.get("raw_json"))
                actions.append({"action": "QUOTE_FILLED", "reason": f"Quote id={quote_id} filled by trade {fill['trade_id']} at paper limit {limit_price:.2f}c."})
                continue
            age_seconds = (now - quote_time).total_seconds()
            if age_seconds >= config.quote_ttl_seconds:
                self.storage.update_paper_market_making_quote(
                    quote_id,
                    {"status": "CANCELLED", "cancel_time": now, "cancel_reason": "quote_ttl_expired"},
                )
                actions.append({"action": "QUOTE_CANCELLED", "reason": f"Quote id={quote_id} cancelled after {age_seconds:.0f}s with no trade-print fill."})

    def _refresh_filled_quotes(self, config: PaperMarketMakerConfig) -> None:
        filled_quotes = self.storage.fetch_sql(
            """
            SELECT *
            FROM paper_market_making_quotes
            WHERE market_ticker = :ticker AND side = :side AND status = 'FILLED'
            ORDER BY quote_time
            """,
            {"ticker": config.market_ticker, "side": config.side},
        )
        if filled_quotes.empty:
            return
        mark = self._current_mark(config.market_ticker, config.side)
        for _, quote in filled_quotes.iterrows():
            fill_time = _parse_ts(quote.get("fill_time"))
            fill_price = _num(quote.get("fill_price_cents"))
            if fill_time is None or fill_price is None:
                continue
            quantity = float(quote.get("quantity") or config.quantity)
            fee = float(quote.get("fee_cents") or self.fee_model.fee_cents(int(round(fill_price)), quantity))
            values = {
                "fee_cents": fee,
                "current_mark_cents": mark,
                "unrealized_pnl_cents": _marked_pnl(config.side, fill_price, quantity, mark, fee),
            }
            values.update(self._future_edges(config.market_ticker, config.side, fill_time, fill_price))
            self.storage.update_paper_market_making_quote(int(quote["id"]), values)

    def _candidate_quote(self, config: PaperMarketMakerConfig, latest_book: dict[str, Any] | None, now: datetime, summary: dict[str, Any]) -> dict[str, Any]:
        if latest_book is None:
            return {"ok": False, "reason": "No recorded orderbook snapshot exists for this market."}
        book_ts = _parse_ts(latest_book.get("ts"))
        if book_ts is None:
            return {"ok": False, "reason": "Latest orderbook timestamp is invalid."}
        age = (now - book_ts).total_seconds()
        if age > config.stale_orderbook_seconds:
            return {"ok": False, "reason": f"Latest orderbook is stale: {age:.0f}s old."}
        status = str(latest_book.get("market_status") or "").lower()
        if status and status not in {"open", "active"}:
            return {"ok": False, "reason": f"Market status is {status}; paper maker will not quote."}
        if summary["open_quotes"] >= config.max_open_quotes:
            return {"ok": False, "reason": f"Already has {summary['open_quotes']} open paper quote(s)."}
        if summary["inventory_quantity"] + config.quantity > config.max_position:
            return {"ok": False, "reason": f"Paper inventory limit would be exceeded: {summary['inventory_quantity']:.2f}/{config.max_position:.2f}."}
        spread = _num(latest_book.get("spread_cents"))
        if spread is None or spread < config.min_spread_cents:
            return {"ok": False, "reason": f"Spread {spread} below minimum {config.min_spread_cents:.2f}c."}
        if config.side == "BUY_YES":
            bid = _num(latest_book.get("yes_best_bid"))
            ask = _num(latest_book.get("yes_best_ask"))
            depth = _num(latest_book.get("depth_yes_bid_1")) or 0.0
        else:
            bid = _num(latest_book.get("no_best_bid"))
            ask = _num(latest_book.get("no_best_ask"))
            depth = _num(latest_book.get("depth_no_bid_1")) or 0.0
        if bid is None or ask is None:
            return {"ok": False, "reason": "Book is missing same-side bid or opposing ask."}
        if depth < config.min_depth:
            return {"ok": False, "reason": f"Displayed depth {depth:.2f} below minimum {config.min_depth:.2f}."}
        limit_price = min(ask - 1.0, bid + config.improve_cents)
        if limit_price <= 0 or limit_price >= ask:
            return {"ok": False, "reason": "Improved quote would cross/touch the opposing ask."}
        return {
            "ok": True,
            "quote": {
                "limit_price_cents": float(limit_price),
                "same_side_bid_cents": float(bid),
                "opposing_ask_cents": float(ask),
                "quote_spread_cents": float(spread),
                "displayed_depth": float(depth),
                "book_ts": book_ts,
            },
        }

    def _insert_quote(self, config: PaperMarketMakerConfig, quote: dict[str, Any], now: datetime) -> int:
        run_id = now.strftime("%Y%m%dT%H%M%SZ")
        payload = {
            "market_ticker": config.market_ticker,
            "side": config.side,
            "quote": quote,
            "config": config.__dict__,
            "execution_type": LIVE_PAPER,
            "edge_type": PASSIVE_LIQUIDITY_SPREAD_EDGE,
        }
        row = {
            "run_id": run_id,
            "market_ticker": config.market_ticker,
            "side": config.side,
            "quote_time": now,
            "limit_price_cents": quote["limit_price_cents"],
            "quantity": config.quantity,
            "status": "OPEN",
            "quote_spread_cents": quote["quote_spread_cents"],
            "same_side_bid_cents": quote["same_side_bid_cents"],
            "opposing_ask_cents": quote["opposing_ask_cents"],
            "displayed_depth": quote["displayed_depth"],
            "strategy_version": STRATEGY_VERSION,
            "reason": f"Passive {config.side} quote improved by {config.improve_cents:.2f}c without crossing ask.",
            "raw_json": json.dumps(payload, default=str),
        }
        quote_id = self.storage.insert_paper_market_making_quote(row)
        self._insert_paper_order_event(config, quote_id, "submitted_paper_no_fill", quote["limit_price_cents"], row, row["raw_json"])
        return quote_id

    def _insert_paper_order_event(self, config: PaperMarketMakerConfig, quote_id: int, fill_status: str, price: float, values: dict[str, Any], raw_json: Any) -> None:
        payload = {
            "paper_market_making_quote_id": quote_id,
            "market_ticker": config.market_ticker,
            "strategy": STRATEGY_NAME,
            "side": config.side,
            "fill_status": fill_status,
            "values": values,
        }
        self.storage.insert_json(
            "paper_orders",
            payload,
            market_ticker=config.market_ticker,
            order_time=self.now_fn(),
            status=fill_status,
            strategy=STRATEGY_NAME,
            edge_type=PASSIVE_LIQUIDITY_SPREAD_EDGE,
            execution_type=LIVE_PAPER,
            action=config.side,
            side=config.side,
            intended_price=price,
            assumed_fill_price=price if fill_status == "filled_paper" else None,
            contracts=config.quantity,
            edge_cents=values.get("future_edge_30m_cents"),
            fill_status=fill_status,
            reason=values.get("reason") or values.get("cancel_reason"),
            raw_json=raw_json if isinstance(raw_json, str) else json.dumps(raw_json, default=str),
        )

    def _fill_for_quote(self, config: PaperMarketMakerConfig, quote, quote_time: datetime, not_after: datetime) -> dict[str, Any] | None:
        trades = self.storage.fetch_sql(
            """
            SELECT ts, trade_id, price, yes_price, no_price, side
            FROM historical_trades
            WHERE market_ticker = :ticker AND ts > :quote_time AND ts <= :not_after
            ORDER BY ts
            """,
            {
                "ticker": config.market_ticker,
                "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                "not_after": not_after.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        if trades.empty:
            return None
        limit_price = float(quote["limit_price_cents"])
        prepared = trades.copy()
        prepared["ts_dt"] = pd.to_datetime(prepared["ts"], errors="coerce", utc=True)
        if config.side == "BUY_YES":
            prepared["trade_price_for_side"] = pd.to_numeric(prepared.get("yes_price", prepared.get("price")), errors="coerce")
        else:
            no_price = pd.to_numeric(prepared.get("no_price", pd.Series(dtype=float)), errors="coerce")
            yes_price = pd.to_numeric(prepared.get("yes_price", prepared.get("price")), errors="coerce")
            prepared["trade_price_for_side"] = no_price.where(no_price.notna(), 100.0 - yes_price)
        hits = prepared[prepared["trade_price_for_side"] <= limit_price].dropna(subset=["ts_dt"])
        if hits.empty:
            return None
        hit = hits.sort_values("ts_dt").iloc[0]
        return {
            "fill_time": hit["ts_dt"].to_pydatetime(),
            "trade_price": _num(hit.get("trade_price_for_side")),
            "trade_id": None if pd.isna(hit.get("trade_id")) else str(hit.get("trade_id")),
        }

    def _future_edges(self, market_ticker: str, config_side: PaperMakerSide, fill_time: datetime, fill_price: float) -> dict[str, float | None]:
        values: dict[str, float | None] = {}
        for minutes in (5, 15, 30, 60):
            future = self.storage.fetch_sql(
                """
                SELECT ts, yes_best_bid, yes_best_ask, no_best_bid, no_best_ask, mid_cents
                FROM orderbook_snapshots_live
                WHERE market_ticker = :ticker AND ts >= :target
                ORDER BY ts
                LIMIT 1
                """,
                {
                    "ticker": market_ticker,
                    "target": (fill_time + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            if future.empty:
                values[f"future_edge_{minutes}m_cents"] = None
                continue
            mark = _side_mark_from_book(future.iloc[0].to_dict(), config_side)
            values[f"future_edge_{minutes}m_cents"] = None if mark is None else float(mark - fill_price)
        return values

    def _current_mark(self, market_ticker: str, side: PaperMakerSide) -> float | None:
        latest = self._latest_book(market_ticker)
        if latest is None:
            return None
        return _side_mark_from_book(latest, side)

    def _latest_book(self, market_ticker: str) -> dict[str, Any] | None:
        frame = self.storage.fetch_sql(
            """
            SELECT *
            FROM orderbook_snapshots_live
            WHERE market_ticker = :ticker
            ORDER BY ts DESC
            LIMIT 1
            """,
            {"ticker": market_ticker},
        )
        if frame.empty:
            return None
        return frame.iloc[0].to_dict()

    def _open_quotes(self, market_ticker: str, side: PaperMakerSide) -> pd.DataFrame:
        return self.storage.fetch_sql(
            """
            SELECT *
            FROM paper_market_making_quotes
            WHERE market_ticker = :ticker AND side = :side AND status = 'OPEN'
            ORDER BY quote_time
            """,
            {"ticker": market_ticker, "side": side},
        )

    def _summary(self, config: PaperMarketMakerConfig, latest_book: dict[str, Any] | None) -> dict[str, Any]:
        rows = self.storage.fetch_sql(
            """
            SELECT *
            FROM paper_market_making_quotes
            WHERE market_ticker = :ticker AND side = :side
            ORDER BY quote_time
            """,
            {"ticker": config.market_ticker, "side": config.side},
        )
        current_mark = _side_mark_from_book(latest_book, config.side) if latest_book else None
        if rows.empty:
            return _empty_summary(config, latest_book, current_mark)
        filled = rows[rows["status"] == "FILLED"].copy()
        open_rows = rows[rows["status"] == "OPEN"].copy()
        cancelled = rows[rows["status"] == "CANCELLED"].copy()
        if not filled.empty:
            filled["fill_price_cents"] = pd.to_numeric(filled["fill_price_cents"], errors="coerce")
            filled["quantity"] = pd.to_numeric(filled["quantity"], errors="coerce").fillna(0.0)
            filled["fee_cents"] = pd.to_numeric(filled["fee_cents"], errors="coerce").fillna(0.0)
        inventory = float(filled["quantity"].sum()) if not filled.empty else 0.0
        avg_fill = float((filled["fill_price_cents"] * filled["quantity"]).sum() / inventory) if inventory else None
        fees = float(filled["fee_cents"].sum()) if not filled.empty else 0.0
        unrealized = _marked_pnl(config.side, avg_fill, inventory, current_mark, fees) if avg_fill is not None else 0.0
        edge_30 = pd.to_numeric(filled.get("future_edge_30m_cents", pd.Series(dtype=float)), errors="coerce").dropna()
        adverse = edge_30[edge_30 < 0]
        latest_ts = _parse_ts(latest_book.get("ts")) if latest_book else None
        return {
            "market_ticker": config.market_ticker,
            "side": config.side,
            "status": "UNKNOWN",
            "message": "",
            "quotes_total": int(len(rows)),
            "open_quotes": int(len(open_rows)),
            "filled_quotes": int(len(filled)),
            "cancelled_quotes": int(len(cancelled)),
            "fill_rate": float(len(filled) / max(len(rows), 1)),
            "inventory_quantity": inventory,
            "avg_fill_price_cents": avg_fill,
            "current_mark_cents": current_mark,
            "unrealized_pnl_cents": unrealized,
            "fees_cents": fees,
            "avg_future_edge_30m_cents": float(edge_30.mean()) if not edge_30.empty else 0.0,
            "future_edge_30m_observations": int(len(edge_30)),
            "adverse_fill_rate_30m": float(len(adverse) / len(edge_30)) if not edge_30.empty else 0.0,
            "latest_orderbook_ts": latest_ts,
            "latest_orderbook_age_seconds": None if latest_ts is None else (self.now_fn() - latest_ts).total_seconds(),
            "last_action": None,
            "last_reason": None,
            "config": config.__dict__,
        }

    def _export(self, config: PaperMarketMakerConfig, summary: dict[str, Any]) -> dict[str, str]:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        safe_ticker = config.market_ticker.replace("/", "_")
        quotes_path = reports / f"paper_market_making_quotes_{safe_ticker}_{config.side}.csv"
        summary_path = reports / f"paper_market_making_summary_{safe_ticker}_{config.side}.json"
        rows = self.storage.fetch_sql(
            """
            SELECT *
            FROM paper_market_making_quotes
            WHERE market_ticker = :ticker AND side = :side
            ORDER BY quote_time
            """,
            {"ticker": config.market_ticker, "side": config.side},
        )
        rows.to_csv(quotes_path, index=False)
        summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return {"quotes": str(quotes_path), "summary": str(summary_path)}


def _empty_summary(config: PaperMarketMakerConfig, latest_book: dict[str, Any] | None, current_mark: float | None) -> dict[str, Any]:
    latest_ts = _parse_ts(latest_book.get("ts")) if latest_book else None
    return {
        "market_ticker": config.market_ticker,
        "side": config.side,
        "status": "UNKNOWN",
        "message": "",
        "quotes_total": 0,
        "open_quotes": 0,
        "filled_quotes": 0,
        "cancelled_quotes": 0,
        "fill_rate": 0.0,
        "inventory_quantity": 0.0,
        "avg_fill_price_cents": None,
        "current_mark_cents": current_mark,
        "unrealized_pnl_cents": 0.0,
        "fees_cents": 0.0,
        "avg_future_edge_30m_cents": 0.0,
        "future_edge_30m_observations": 0,
        "adverse_fill_rate_30m": 0.0,
        "latest_orderbook_ts": latest_ts,
        "latest_orderbook_age_seconds": None,
        "last_action": None,
        "last_reason": None,
        "config": config.__dict__,
    }


def _summary_status(summary: dict[str, Any]) -> str:
    if summary.get("quotes_total", 0) == 0 and summary.get("last_action") == "NO_QUOTE":
        return "PAPER_WAITING_FOR_SETUP"
    if (
        summary.get("filled_quotes", 0) >= 10
        and summary.get("future_edge_30m_observations", 0) >= 10
        and summary.get("avg_future_edge_30m_cents", 0.0) > 0
        and summary.get("unrealized_pnl_cents", 0.0) > 0
        and summary.get("adverse_fill_rate_30m", 1.0) <= 0.25
    ):
        return "PAPER_POSITIVE_MONITOR"
    if summary.get("filled_quotes", 0) > 0:
        return "PAPER_ACTIVE_COLLECTING_FILLS"
    return "PAPER_ACTIVE_NO_FILLS_YET"


def _summary_message(summary: dict[str, Any]) -> str:
    if summary.get("status") == "PAPER_WAITING_FOR_SETUP":
        return f"Paper maker is alive but has not opened a quote: {summary.get('last_reason')}"
    if summary.get("status") == "PAPER_POSITIVE_MONITOR":
        return "Paper market maker is positive so far; continue paper only and expand only after more fills."
    if summary.get("filled_quotes", 0) > 0:
        return "Paper fills exist; keep collecting before increasing size."
    return "Paper quotes are being tracked; no trade-print fills yet."


def _side_mark_from_book(book: dict[str, Any], side: PaperMakerSide) -> float | None:
    if side == "BUY_YES":
        mark = _num(book.get("mid_cents"))
        if mark is not None:
            return mark
        bid = _num(book.get("yes_best_bid"))
        ask = _num(book.get("yes_best_ask"))
    else:
        no_bid = _num(book.get("no_best_bid"))
        no_ask = _num(book.get("no_best_ask"))
        if no_bid is not None and no_ask is not None:
            return (no_bid + no_ask) / 2.0
        yes_mid = _num(book.get("mid_cents"))
        if yes_mid is not None:
            return 100.0 - yes_mid
        bid = _num(book.get("no_best_bid"))
        ask = _num(book.get("no_best_ask"))
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _marked_pnl(side: PaperMakerSide, avg_fill: float | None, quantity: float, current_mark: float | None, fees: float) -> float:
    if avg_fill is None or current_mark is None or quantity <= 0:
        return 0.0 - fees
    return (current_mark - avg_fill) * quantity - fees


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


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _num(value)
    return "none" if number is None else f"{number:.2f}"


def _print_progress(result: PaperMarketMakerResult) -> None:
    summary = result.summary
    action = summary.get("last_action") or "UNKNOWN"
    reason = summary.get("last_reason") or ""
    print(
        "PAPER_MM HEARTBEAT "
        f"status={summary.get('status')} "
        f"market={summary.get('market_ticker')} side={summary.get('side')} "
        f"quotes={summary.get('quotes_total')} open={summary.get('open_quotes')} "
        f"filled={summary.get('filled_quotes')} cancelled={summary.get('cancelled_quotes')} "
        f"inventory={summary.get('inventory_quantity'):.2f} mark={_fmt(summary.get('current_mark_cents'))} "
        f"avg_edge30={_fmt(summary.get('avg_future_edge_30m_cents'))} "
        f"last_action={action} reason={reason}",
        flush=True,
    )
