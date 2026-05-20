from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text

from backtest.fees import ConservativeFixedFeeModel
from config import PROJECT_ROOT
from data.storage import Storage


DISCLAIMER = (
    "Paper market-making evidence is research-only. It uses local paper quote records and stored future-edge/markout "
    "fields; it is not live trading readiness, not a profitability claim, and not proof of executable live fills."
)


@dataclass(frozen=True)
class PaperMarketMakingEvidenceConfig:
    stale_open_seconds: int = 600
    too_few_fills_threshold: int = 5
    adverse_high_threshold: float = 0.35
    last_days: int | None = None
    since: datetime | None = None
    timestamped_export: bool = False


@dataclass(frozen=True)
class PaperMarketMakingEvidenceResult:
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        lines = [
            f"paper_market_making_evidence_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"market_sides={self.summary.get('market_sides')} quotes={self.summary.get('quotes_total')} "
            f"open={self.summary.get('open_quotes')} filled={self.summary.get('filled_quotes')} "
            f"cancelled={self.summary.get('cancelled_quotes')}",
            f"fill_rate={_fmt(self.summary.get('fill_rate'))} avg_net_30m={_fmt(self.summary.get('avg_net_markout_30m_cents'))} "
            f"future30_n={self.summary.get('future_edge_30m_observations')} adverse30={_fmt(self.summary.get('adverse_selection_rate_30m'))}",
            f"fee_sources=stored:{self.summary.get('stored_fee_count')} estimated:{self.summary.get('estimated_fee_count')} "
            f"missing:{self.summary.get('missing_fee_count')} estimated_share={_fmt(self.summary.get('estimated_fee_share'))}",
            f"window_start={self.summary.get('window_start')} window_end={self.summary.get('window_end')} "
            f"export_mode={self.summary.get('export_mode')}",
            f"exports={self.exports}",
            f"disclaimer={DISCLAIMER}",
        ]
        lines.append("Top good candidates:")
        for row in self.summary.get("top_good_candidates", [])[:5]:
            lines.append(
                f"- {row.get('market_ticker')} {row.get('side')} fills={row.get('quotes_filled')} "
                f"net30={_fmt(row.get('avg_net_markout_30m_cents'))} adverse30={_fmt(row.get('adverse_selection_rate_30m'))} "
                f"flags={row.get('warning_flags')}"
            )
        lines.append("Top red flags:")
        for row in self.summary.get("top_red_flags", [])[:5]:
            lines.append(
                f"- {row.get('market_ticker')} {row.get('side')} fills={row.get('quotes_filled')} "
                f"net30={_fmt(row.get('avg_net_markout_30m_cents'))} flags={row.get('warning_flags')}"
            )
        return "\n".join(lines)


class PaperMarketMakingEvidenceReporter:
    """Read-only cumulative evidence report for paper market-making quotes."""

    def __init__(self, storage: Storage | None = None, now_fn: Callable[[], datetime] | None = None):
        self.storage = storage or Storage()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.fee_model = ConservativeFixedFeeModel()

    def build(
        self,
        config: PaperMarketMakingEvidenceConfig | None = None,
        *,
        persist_exports: bool = True,
    ) -> PaperMarketMakingEvidenceResult:
        config = config or PaperMarketMakingEvidenceConfig()
        now = self.now_fn()
        window_start = _window_start(config, now)
        self.storage.init_db()
        quotes = self._read_quotes(window_start)
        if quotes.empty:
            summary = _empty_summary(now, config, window_start)
            exports = self._export(summary, [], persist_exports, config) if persist_exports else None
            return PaperMarketMakingEvidenceResult(summary=summary, rows=[], exports=exports)
        quotes = _prepare_quotes(quotes)
        latest_books = self._latest_books(sorted(set(quotes["market_ticker"].dropna().astype(str))))
        rows = [_summarize_group(group, latest_books, config, now, self.fee_model) for _, group in quotes.groupby(["market_ticker", "side"], dropna=False)]
        rows.sort(key=_good_candidate_sort_key, reverse=True)
        summary = _summary(rows, now, config, window_start)
        exports = self._export(summary, rows, persist_exports, config) if persist_exports else None
        return PaperMarketMakingEvidenceResult(summary=summary, rows=rows, exports=exports)

    def _read_quotes(self, window_start: datetime | None) -> pd.DataFrame:
        sql = """
        SELECT *
        FROM paper_market_making_quotes
        WHERE (:window_start IS NULL OR quote_time >= :window_start)
        ORDER BY quote_time
        """
        with self.storage.engine.connect() as conn:
            return pd.read_sql_query(
                text(sql),
                conn,
                params={"window_start": None if window_start is None else window_start.strftime("%Y-%m-%d %H:%M:%S")},
            )

    def _latest_books(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        if not tickers:
            return {}
        params = {f"ticker_{idx}": ticker for idx, ticker in enumerate(tickers)}
        placeholders = ", ".join(f":{key}" for key in params)
        sql = f"""
        SELECT *
        FROM (
            SELECT
                market_ticker,
                ts,
                market_status,
                market_close_time,
                ROW_NUMBER() OVER (PARTITION BY market_ticker ORDER BY ts DESC) AS rn
            FROM orderbook_snapshots_live
            WHERE market_ticker IN ({placeholders})
        )
        WHERE rn = 1
        """
        with self.storage.engine.connect() as conn:
            frame = pd.read_sql_query(text(sql), conn, params=params)
        if frame.empty:
            return {}
        return {str(row["market_ticker"]): row.to_dict() for _, row in frame.iterrows()}

    def _export(
        self,
        summary: dict[str, Any],
        rows: list[dict[str, Any]],
        persist_exports: bool,
        config: PaperMarketMakingEvidenceConfig,
    ) -> dict[str, str] | None:
        if not persist_exports:
            return None
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        suffix = ""
        if config.timestamped_export:
            suffix = f"_{_export_stamp(summary.get('generated_at'))}"
        csv_path = reports / f"paper_market_making_evidence{suffix}.csv"
        json_path = reports / f"paper_market_making_evidence{suffix}.json"
        md_path = reports / f"paper_market_making_evidence{suffix}.md"
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        payload = {"summary": summary, "rows": rows, "disclaimer": DISCLAIMER}
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        md_path.write_text(_markdown(summary, rows), encoding="utf-8")
        return {"csv": str(csv_path), "json": str(json_path), "markdown": str(md_path)}


def _prepare_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    prepared = quotes.copy()
    for column in ("quote_time", "fill_time", "cancel_time"):
        if column in prepared:
            prepared[f"{column}_dt"] = pd.to_datetime(prepared[column], errors="coerce", utc=True)
    for column in (
        "limit_price_cents",
        "quantity",
        "fill_price_cents",
        "fill_trade_price_cents",
        "fee_cents",
        "current_mark_cents",
        "unrealized_pnl_cents",
        "future_edge_5m_cents",
        "future_edge_15m_cents",
        "future_edge_30m_cents",
        "future_edge_60m_cents",
        "quote_spread_cents",
        "same_side_bid_cents",
        "opposing_ask_cents",
        "displayed_depth",
    ):
        if column in prepared:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    return prepared


def _summarize_group(
    group: pd.DataFrame,
    latest_books: dict[str, dict[str, Any]],
    config: PaperMarketMakingEvidenceConfig,
    now: datetime,
    fee_model: ConservativeFixedFeeModel,
) -> dict[str, Any]:
    market_ticker = str(group.iloc[0]["market_ticker"])
    side = str(group.iloc[0]["side"])
    statuses = group["status"].astype(str).str.upper()
    filled = group[statuses == "FILLED"].copy()
    open_rows = group[statuses == "OPEN"].copy()
    cancelled = group[statuses == "CANCELLED"].copy()
    stale_open = _stale_open_count(open_rows, config.stale_open_seconds, now)
    source_tier = _source_tier(group)
    fee_missing = bool(not filled.empty and filled["fee_cents"].isna().any())
    if not filled.empty:
        filled["quantity"] = filled["quantity"].fillna(1.0)
        fee_parts = filled.apply(lambda row: _effective_fee_parts(row, fee_model), axis=1, result_type="expand")
        fee_parts.columns = ["effective_fee_cents", "fee_source_detail"]
        filled = pd.concat([filled, fee_parts], axis=1)
    stored_fee_count = int((filled.get("fee_source_detail", pd.Series(dtype=str)) == "stored").sum()) if not filled.empty else 0
    estimated_fee_count = int((filled.get("fee_source_detail", pd.Series(dtype=str)) == "estimated").sum()) if not filled.empty else 0
    latest_book = latest_books.get(market_ticker)
    latest_book_ts = _parse_ts(latest_book.get("ts")) if latest_book else None
    latest_status = str(latest_book.get("market_status") or "").lower() if latest_book else None
    latest_close = _parse_ts(latest_book.get("market_close_time")) if latest_book else None
    current_unrealized = _sum_or_none(filled.get("unrealized_pnl_cents", pd.Series(dtype=float)))
    markouts = {}
    for minutes in (5, 15, 30, 60):
        markouts.update(_markout_metrics(filled, minutes))
    adverse_30 = _adverse_rate(filled.get("future_edge_30m_cents", pd.Series(dtype=float)))
    flags = _warning_flags(
        filled=filled,
        open_rows=open_rows,
        all_rows=group,
        source_tier=source_tier,
        stale_open=stale_open,
        adverse_30=adverse_30,
        current_unrealized=current_unrealized,
        latest_book=latest_book,
        latest_status=latest_status,
        latest_close=latest_close,
        fee_missing=fee_missing,
        config=config,
        now=now,
    )
    return {
        "market_ticker": market_ticker,
        "side": side,
        "source_tier": source_tier,
        "quotes_total": int(len(group)),
        "quotes_opened": int(len(group)),  # Backward-compatible alias; reports use quotes_total.
        "quotes_cancelled": int(len(cancelled)),
        "quotes_filled": int(len(filled)),
        "open_quotes": int(len(open_rows)),
        "fill_rate": float(len(filled) / max(len(group), 1)),
        "stale_open_quotes": int(stale_open),
        "avg_quote_spread_cents": _mean_or_none(group.get("quote_spread_cents", pd.Series(dtype=float))),
        "avg_displayed_depth": _mean_or_none(group.get("displayed_depth", pd.Series(dtype=float))),
        "total_fees_cents": _sum_or_none(filled.get("effective_fee_cents", pd.Series(dtype=float))),
        "stored_fee_count": stored_fee_count,
        "estimated_fee_count": estimated_fee_count,
        "missing_fee_count": 0,
        "fee_source": "mixed_stored_and_estimated" if estimated_fee_count and stored_fee_count else ("estimated" if estimated_fee_count else "stored"),
        "current_unrealized_pnl_cents": current_unrealized,
        "adverse_selection_rate_30m": adverse_30,
        "latest_orderbook_ts": None if latest_book_ts is None else latest_book_ts.isoformat(),
        "latest_market_status": latest_status,
        "latest_market_close_time": None if latest_close is None else latest_close.isoformat(),
        "warning_flags": ";".join(flags),
        **markouts,
    }


def _markout_metrics(filled: pd.DataFrame, minutes: int) -> dict[str, Any]:
    column = f"future_edge_{minutes}m_cents"
    if filled.empty or column not in filled:
        return {
            f"gross_markout_{minutes}m_observations": 0,
            f"avg_gross_markout_{minutes}m_cents": None,
            f"avg_net_markout_{minutes}m_cents": None,
        }
    observed = filled.dropna(subset=[column]).copy()
    if observed.empty:
        return {
            f"gross_markout_{minutes}m_observations": 0,
            f"avg_gross_markout_{minutes}m_cents": None,
            f"avg_net_markout_{minutes}m_cents": None,
        }
    observed["quantity"] = observed["quantity"].fillna(1.0)
    observed["net_markout_cents"] = observed[column] * observed["quantity"] - observed["effective_fee_cents"]
    return {
        f"gross_markout_{minutes}m_observations": int(len(observed)),
        f"avg_gross_markout_{minutes}m_cents": float(observed[column].mean()),
        f"avg_net_markout_{minutes}m_cents": float(observed["net_markout_cents"].mean()),
    }


def _warning_flags(
    *,
    filled: pd.DataFrame,
    open_rows: pd.DataFrame,
    all_rows: pd.DataFrame,
    source_tier: str,
    stale_open: int,
    adverse_30: float | None,
    current_unrealized: float | None,
    latest_book: dict[str, Any] | None,
    latest_status: str | None,
    latest_close: datetime | None,
    fee_missing: bool,
    config: PaperMarketMakingEvidenceConfig,
    now: datetime,
) -> list[str]:
    flags: list[str] = []
    if not filled.empty and filled.get("future_edge_30m_cents", pd.Series(dtype=float)).notna().sum() < len(filled):
        flags.append("missing_30m_markout")
    if stale_open > 0:
        flags.append("stale_open_quote")
    if len(filled) < config.too_few_fills_threshold:
        flags.append("too_few_fills")
    if adverse_30 is not None and adverse_30 >= config.adverse_high_threshold:
        flags.append("adverse_selection_high")
    if source_tier == "EXPLORATORY_CURRENT":
        flags.append("exploratory_target")
    if current_unrealized is not None and current_unrealized < 0:
        flags.append("current_unrealized_negative")
    if latest_book is None or (latest_status and latest_status not in {"open", "active"}) or (latest_close is not None and latest_close < now):
        flags.append("stale_market")
    if fee_missing:
        flags.append("missing_fee_data")
    if all_rows.get("displayed_depth", pd.Series(dtype=float)).isna().any():
        flags.append("missing_depth_data")
    return flags


def _summary(
    rows: list[dict[str, Any]],
    now: datetime,
    config: PaperMarketMakingEvidenceConfig,
    window_start: datetime | None,
) -> dict[str, Any]:
    quotes_total = sum(int(row["quotes_total"]) for row in rows)
    filled_total = sum(int(row["quotes_filled"]) for row in rows)
    cancelled_total = sum(int(row["quotes_cancelled"]) for row in rows)
    open_total = sum(int(row["open_quotes"]) for row in rows)
    observed_30 = sum(int(row.get("gross_markout_30m_observations") or 0) for row in rows)
    weighted_net_30 = [
        (float(row["avg_net_markout_30m_cents"]), int(row.get("gross_markout_30m_observations") or 0))
        for row in rows
        if _num(row.get("avg_net_markout_30m_cents")) is not None and int(row.get("gross_markout_30m_observations") or 0) > 0
    ]
    avg_net_30 = _weighted_average(weighted_net_30)
    weighted_adverse = [
        (float(row["adverse_selection_rate_30m"]), int(row.get("gross_markout_30m_observations") or 0))
        for row in rows
        if _num(row.get("adverse_selection_rate_30m")) is not None and int(row.get("gross_markout_30m_observations") or 0) > 0
    ]
    adverse = _weighted_average(weighted_adverse)
    top_good = [
        row
        for row in rows
        if int(row.get("quotes_filled") or 0) > 0
        and _num(row.get("avg_net_markout_30m_cents")) is not None
        and float(row["avg_net_markout_30m_cents"]) > 0
    ][:10]
    top_red = sorted(rows, key=lambda row: (_flag_count(row.get("warning_flags")), int(row.get("quotes_filled") or 0)), reverse=True)[:10]
    stored_fee_count = sum(int(row.get("stored_fee_count") or 0) for row in rows)
    estimated_fee_count = sum(int(row.get("estimated_fee_count") or 0) for row in rows)
    missing_fee_count = sum(int(row.get("missing_fee_count") or 0) for row in rows)
    fee_denom = stored_fee_count + estimated_fee_count + missing_fee_count
    status = _status(filled_total, observed_30, avg_net_30, adverse, config)
    return {
        "status": status,
        "message": _message(status),
        "generated_at": now.isoformat(),
        "window_start": None if window_start is None else window_start.isoformat(),
        "window_end": now.isoformat(),
        "window_description": "full_history" if window_start is None else "filtered_by_quote_time",
        "export_mode": "timestamped" if config.timestamped_export else "overwrite_default_paths",
        "market_sides": len(rows),
        "quotes_total": quotes_total,
        "open_quotes": open_total,
        "filled_quotes": filled_total,
        "cancelled_quotes": cancelled_total,
        "fill_rate": float(filled_total / max(quotes_total, 1)),
        "avg_net_markout_30m_cents": avg_net_30,
        "future_edge_30m_observations": observed_30,
        "adverse_selection_rate_30m": adverse,
        "stored_fee_count": stored_fee_count,
        "estimated_fee_count": estimated_fee_count,
        "missing_fee_count": missing_fee_count,
        "estimated_fee_share": None if fee_denom == 0 else float(estimated_fee_count / fee_denom),
        "fee_source_note": "Net markouts can mix stored fees and conservative estimated fees; inspect stored_fee_count and estimated_fee_count before trusting averages.",
        "config": config.__dict__,
        "top_good_candidates": top_good,
        "top_red_flags": top_red,
    }


def _empty_summary(now: datetime, config: PaperMarketMakingEvidenceConfig, window_start: datetime | None) -> dict[str, Any]:
    return {
        "status": "NO_PAPER_MARKET_MAKING_EVIDENCE",
        "message": "No paper-market-making quote rows exist yet.",
        "generated_at": now.isoformat(),
        "window_start": None if window_start is None else window_start.isoformat(),
        "window_end": now.isoformat(),
        "window_description": "full_history" if window_start is None else "filtered_by_quote_time",
        "export_mode": "timestamped" if config.timestamped_export else "overwrite_default_paths",
        "market_sides": 0,
        "quotes_total": 0,
        "open_quotes": 0,
        "filled_quotes": 0,
        "cancelled_quotes": 0,
        "fill_rate": 0.0,
        "avg_net_markout_30m_cents": None,
        "future_edge_30m_observations": 0,
        "adverse_selection_rate_30m": None,
        "stored_fee_count": 0,
        "estimated_fee_count": 0,
        "missing_fee_count": 0,
        "estimated_fee_share": None,
        "fee_source_note": "No filled quotes were available for fee-source accounting.",
        "top_good_candidates": [],
        "top_red_flags": [],
    }


def _status(
    filled_total: int,
    observed_30: int,
    avg_net_30: float | None,
    adverse: float | None,
    config: PaperMarketMakingEvidenceConfig,
) -> str:
    if filled_total == 0:
        return "COLLECT_MORE_TRADE_EVIDENCE"
    if observed_30 < max(config.too_few_fills_threshold, 5):
        return "PAPER_EVIDENCE_INCOMPLETE"
    if avg_net_30 is None or avg_net_30 <= 0 or (adverse is not None and adverse >= config.adverse_high_threshold):
        return "PAPER_EVIDENCE_WEAK_OR_DETERIORATING"
    return "PAPER_EVIDENCE_PROMISING_NOT_READY"


def _message(status: str) -> str:
    return {
        "COLLECT_MORE_TRADE_EVIDENCE": "Paper quotes exist but fills are too sparse for confidence.",
        "PAPER_EVIDENCE_INCOMPLETE": "Paper fills exist but markouts are incomplete or too few.",
        "PAPER_EVIDENCE_WEAK_OR_DETERIORATING": "Paper evidence is weak, adverse, or negative after fees.",
        "PAPER_EVIDENCE_PROMISING_NOT_READY": "Paper evidence is positive but remains research-only and not live-trading readiness.",
    }.get(status, "Paper evidence report generated.")


def _markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Paper Market-Making Evidence",
        "",
        DISCLAIMER,
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Window: {summary.get('window_description')} from {summary.get('window_start')} to {summary.get('window_end')}",
        f"- Quotes: {summary.get('quotes_total')} total, {summary.get('filled_quotes')} filled, {summary.get('cancelled_quotes')} cancelled, {summary.get('open_quotes')} open",
        f"- Fill rate: {_fmt(summary.get('fill_rate'))}",
        f"- Avg net 30m markout: {_fmt(summary.get('avg_net_markout_30m_cents'))}c",
        f"- 30m observations: {summary.get('future_edge_30m_observations')}",
        f"- Adverse 30m rate: {_fmt(summary.get('adverse_selection_rate_30m'))}",
        f"- Fee sources: stored={summary.get('stored_fee_count')}, estimated={summary.get('estimated_fee_count')}, missing={summary.get('missing_fee_count')}, estimated share={_fmt(summary.get('estimated_fee_share'))}",
        f"- Fee note: {summary.get('fee_source_note')}",
        f"- Export mode: {summary.get('export_mode')}",
        "",
        "## Top Good Candidates",
        "",
        "| Market | Side | Fills | Net 30m | Adverse 30m | Flags |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in summary.get("top_good_candidates", [])[:10]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('side')} | {row.get('quotes_filled')} | {_fmt(row.get('avg_net_markout_30m_cents'))} | {_fmt(row.get('adverse_selection_rate_30m'))} | {row.get('warning_flags')} |")
    lines.extend(["", "## Red Flags", "", "| Market | Side | Fills | Net 30m | Flags |", "|---|---:|---:|---:|---|"])
    for row in summary.get("top_red_flags", [])[:10]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('side')} | {row.get('quotes_filled')} | {_fmt(row.get('avg_net_markout_30m_cents'))} | {row.get('warning_flags')} |")
    lines.extend(["", "## All Market/Sides", "", "| Market | Side | Quotes Total | Open | Fills | Fill Rate | Net 30m | Fee Source | Flags |", "|---|---:|---:|---:|---:|---:|---:|---|---|"])
    for row in rows:
        lines.append(f"| {row.get('market_ticker')} | {row.get('side')} | {row.get('quotes_total')} | {row.get('open_quotes')} | {row.get('quotes_filled')} | {_fmt(row.get('fill_rate'))} | {_fmt(row.get('avg_net_markout_30m_cents'))} | {row.get('fee_source')} | {row.get('warning_flags')} |")
    return "\n".join(lines) + "\n"


def _stale_open_count(open_rows: pd.DataFrame, stale_open_seconds: int, now: datetime) -> int:
    if open_rows.empty or "quote_time_dt" not in open_rows:
        return 0
    ages = (pd.Timestamp(now) - open_rows["quote_time_dt"]).dt.total_seconds()
    return int((ages > stale_open_seconds).sum())


def _effective_fee_parts(row: pd.Series, fee_model: ConservativeFixedFeeModel) -> tuple[float, str]:
    stored = _num(row.get("fee_cents"))
    if stored is not None:
        return stored, "stored"
    price = _num(row.get("fill_price_cents")) or _num(row.get("limit_price_cents")) or 50.0
    quantity = _num(row.get("quantity")) or 1.0
    return float(fee_model.fee_cents(int(round(price)), quantity)), "estimated"


def _adverse_rate(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float((numeric < 0).sum() / len(numeric))


def _source_tier(rows: pd.DataFrame) -> str:
    for raw in rows.get("raw_json", pd.Series(dtype=str)).dropna():
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            continue
        for key in ("tier", "source_tier", "selection_tier"):
            if payload.get(key):
                return str(payload[key])
        target = payload.get("target")
        if isinstance(target, dict) and target.get("tier"):
            return str(target["tier"])
    return "UNKNOWN"


def _sum_or_none(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.sum())


def _weighted_average(values: list[tuple[float, int]]) -> float | None:
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return float(sum(value * weight for value, weight in values) / total_weight)


def _good_candidate_sort_key(row: dict[str, Any]) -> tuple[bool, float, int]:
    value = _num(row.get("avg_net_markout_30m_cents"))
    return (value is not None, -999.0 if value is None else value, int(row.get("quotes_filled") or 0))


def _flag_count(value: Any) -> int:
    if not value:
        return 0
    return len([flag for flag in str(value).split(";") if flag])


def _window_start(config: PaperMarketMakingEvidenceConfig, now: datetime) -> datetime | None:
    if config.since is not None:
        since = config.since
        return since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    if config.last_days is not None:
        return now - timedelta(days=max(int(config.last_days), 0))
    return None


def _export_stamp(value: Any) -> str:
    parsed = _parse_ts(value)
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    return parsed.strftime("%Y%m%d_%H%M%S")


def _mean_or_none(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.mean())


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


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
    return "none" if number is None else f"{number:.3f}"
