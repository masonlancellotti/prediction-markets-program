from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text

from backtest.fees import ConservativeFixedFeeModel
from data.storage import Storage
from research.paper_market_making_evidence import _effective_fee_parts, _fmt, _num, _prepare_quotes


DISCLAIMER = (
    "Paper market-making drilldown is read-only research evidence. It is not trading readiness, "
    "not a profitability claim, and not proof of executable live fills."
)


@dataclass(frozen=True)
class PaperMarketMakingDrilldownConfig:
    ticker: str
    side: str
    stale_open_seconds: int = 600


@dataclass(frozen=True)
class PaperMarketMakingDrilldownResult:
    summary: dict[str, Any]
    rows: list[dict[str, Any]]

    def to_text(self) -> str:
        lines = [
            f"paper_market_making_drilldown_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"ticker={self.summary.get('ticker')} side={self.summary.get('side')}",
            f"quotes_total={self.summary.get('quotes_total')} open={self.summary.get('open_quotes')} "
            f"filled={self.summary.get('filled_quotes')} cancelled={self.summary.get('cancelled_quotes')} "
            f"fill_rate={_fmt(self.summary.get('fill_rate'))}",
            f"avg_net_30m={_fmt(self.summary.get('avg_net_markout_30m_cents'))} "
            f"future30_n={self.summary.get('future_edge_30m_observations')} "
            f"adverse30={_fmt(self.summary.get('adverse_selection_rate_30m'))}",
            f"disclaimer={DISCLAIMER}",
            "Quotes:",
        ]
        for row in self.rows:
            lines.append(
                f"- id={row.get('quote_id')} open={row.get('quote_time')} price={_fmt(row.get('limit_price_cents'))} "
                f"status={row.get('status')} cancel_age_sec={_fmt(row.get('cancel_age_seconds'))} "
                f"fill_time={row.get('fill_time')} fee={_fmt(row.get('fee_cents'))} "
                f"net30={_fmt(row.get('net_markout_30m_cents'))} unrealized={_fmt(row.get('current_unrealized_pnl_cents'))} "
                f"flags={row.get('warning_flags')}"
            )
        return "\n".join(lines)


class PaperMarketMakingDrilldownReporter:
    """Read-only per quote/fill drilldown for one paper market-making target."""

    def __init__(self, storage: Storage | None = None, now_fn: Callable[[], datetime] | None = None):
        self.storage = storage or Storage()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.fee_model = ConservativeFixedFeeModel()

    def build(self, config: PaperMarketMakingDrilldownConfig) -> PaperMarketMakingDrilldownResult:
        self.storage.init_db()
        ticker = config.ticker.strip().upper()
        side = config.side.strip().upper()
        quotes = self._read_quotes(ticker, side)
        now = self.now_fn()
        if quotes.empty:
            summary = {
                "status": "NO_PAPER_QUOTES_FOUND",
                "message": "No paper market-making quotes found for this ticker/side.",
                "ticker": ticker,
                "side": side,
                "quotes_total": 0,
                "open_quotes": 0,
                "filled_quotes": 0,
                "cancelled_quotes": 0,
                "fill_rate": 0.0,
                "avg_net_markout_30m_cents": None,
                "future_edge_30m_observations": 0,
                "adverse_selection_rate_30m": None,
            }
            return PaperMarketMakingDrilldownResult(summary=summary, rows=[])
        prepared = _prepare_quotes(quotes)
        rows = [self._quote_row(row, config, now) for _, row in prepared.iterrows()]
        summary = _summary(ticker, side, rows)
        return PaperMarketMakingDrilldownResult(summary=summary, rows=rows)

    def _read_quotes(self, ticker: str, side: str) -> pd.DataFrame:
        with self.storage.engine.connect() as conn:
            return pd.read_sql_query(
                text(
                    """
                    SELECT *
                    FROM paper_market_making_quotes
                    WHERE market_ticker = :ticker AND side = :side
                    ORDER BY quote_time, id
                    """
                ),
                conn,
                params={"ticker": ticker, "side": side},
            )

    def _quote_row(self, row: pd.Series, config: PaperMarketMakingDrilldownConfig, now: datetime) -> dict[str, Any]:
        status = str(row.get("status") or "").upper()
        quote_time = _ts(row.get("quote_time_dt"))
        fill_time = _ts(row.get("fill_time_dt"))
        cancel_time = _ts(row.get("cancel_time_dt"))
        fee_cents, fee_source = _effective_fee_parts(row, self.fee_model) if status == "FILLED" else (_num(row.get("fee_cents")), "stored" if _num(row.get("fee_cents")) is not None else "missing")
        net_markouts = {}
        for minutes in (5, 15, 30, 60):
            gross = _num(row.get(f"future_edge_{minutes}m_cents"))
            quantity = _num(row.get("quantity")) or 1.0
            net_markouts[f"net_markout_{minutes}m_cents"] = None if gross is None or fee_cents is None else gross * quantity - fee_cents
        warning_flags = _warning_flags(row, status, quote_time, fee_cents, config, now)
        return {
            "quote_id": int(row.get("id")) if _num(row.get("id")) is not None else None,
            "ticker": str(row.get("market_ticker")),
            "side": str(row.get("side")),
            "quote_time": None if quote_time is None else quote_time.isoformat(),
            "limit_price_cents": _num(row.get("limit_price_cents")),
            "quantity": _num(row.get("quantity")),
            "status": status,
            "cancel_time": None if cancel_time is None else cancel_time.isoformat(),
            "cancel_age_seconds": _age_seconds(quote_time, cancel_time),
            "cancel_reason": row.get("cancel_reason"),
            "fill_time": None if fill_time is None else fill_time.isoformat(),
            "fill_price_cents": _num(row.get("fill_price_cents")),
            "fill_trade_price_cents": _num(row.get("fill_trade_price_cents")),
            "future_edge_5m_cents": _num(row.get("future_edge_5m_cents")),
            "future_edge_15m_cents": _num(row.get("future_edge_15m_cents")),
            "future_edge_30m_cents": _num(row.get("future_edge_30m_cents")),
            "future_edge_60m_cents": _num(row.get("future_edge_60m_cents")),
            "fee_cents": fee_cents,
            "fee_source": fee_source,
            "current_unrealized_pnl_cents": _num(row.get("unrealized_pnl_cents")),
            "quote_spread_cents": _num(row.get("quote_spread_cents")),
            "displayed_depth": _num(row.get("displayed_depth")),
            "warning_flags": ";".join(warning_flags),
            **net_markouts,
        }


def _summary(ticker: str, side: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    open_count = sum(1 for row in rows if row["status"] == "OPEN")
    filled_count = sum(1 for row in rows if row["status"] == "FILLED")
    cancelled_count = sum(1 for row in rows if row["status"] == "CANCELLED")
    net30 = [row["net_markout_30m_cents"] for row in rows if row["net_markout_30m_cents"] is not None]
    future30 = [row["future_edge_30m_cents"] for row in rows if row["future_edge_30m_cents"] is not None]
    adverse = None if not future30 else sum(1 for value in future30 if value < 0) / len(future30)
    return {
        "status": "PAPER_DRILLDOWN_OK",
        "message": "Read-only paper market-making quote drilldown generated.",
        "ticker": ticker,
        "side": side,
        "quotes_total": total,
        "open_quotes": open_count,
        "filled_quotes": filled_count,
        "cancelled_quotes": cancelled_count,
        "fill_rate": filled_count / max(total, 1),
        "avg_net_markout_30m_cents": None if not net30 else sum(net30) / len(net30),
        "future_edge_30m_observations": len(future30),
        "adverse_selection_rate_30m": adverse,
        "warning_flags": sorted({flag for row in rows for flag in str(row.get("warning_flags") or "").split(";") if flag}),
    }


def _warning_flags(
    row: pd.Series,
    status: str,
    quote_time: datetime | None,
    fee_cents: float | None,
    config: PaperMarketMakingDrilldownConfig,
    now: datetime,
) -> list[str]:
    flags: list[str] = []
    if status == "OPEN" and quote_time is not None and (now - quote_time).total_seconds() > config.stale_open_seconds:
        flags.append("stale_open_quote")
    if status == "FILLED" and _num(row.get("future_edge_30m_cents")) is None:
        flags.append("missing_30m_markout")
    if _num(row.get("future_edge_30m_cents")) is not None and float(row.get("future_edge_30m_cents")) < 0:
        flags.append("adverse_30m")
    if _num(row.get("unrealized_pnl_cents")) is not None and float(row.get("unrealized_pnl_cents")) < 0:
        flags.append("current_unrealized_negative")
    if fee_cents is None:
        flags.append("missing_fee_data")
    if _num(row.get("displayed_depth")) is None:
        flags.append("missing_depth_data")
    return flags


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _age_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return float((end - start).total_seconds())
