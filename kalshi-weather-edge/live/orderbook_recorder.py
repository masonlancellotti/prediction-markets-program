from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backtest.execution import NormalizedOrderBook
from config import Settings, settings
from data.kalshi_client import KalshiClient
from data.kalshi_historical_loader import _normalize_trade
from data.kalshi_market_loader import KalshiMarketLoader
from data.storage import Storage

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderbookRecordResult:
    started_at: datetime
    finished_at: datetime
    cycles: int
    snapshots: int
    markets_seen: int
    last_snapshot_at: datetime | None
    stopped_by_user: bool
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "cycles": self.cycles,
            "snapshots": self.snapshots,
            "markets_seen": self.markets_seen,
            "last_snapshot_at": self.last_snapshot_at.isoformat() if self.last_snapshot_at else None,
            "stopped_by_user": self.stopped_by_user,
            "warnings": self.warnings,
        }


class LiveOrderbookRecorder:
    """Record current Kalshi full orderbooks for future passive replay."""

    def __init__(self, client: KalshiClient | None = None, storage: Storage | None = None, cfg: Settings = settings):
        self.client = client or KalshiClient()
        self.storage = storage or Storage()
        self.cfg = cfg
        self.loader = KalshiMarketLoader(client=self.client, storage=self.storage)
        self.closed_or_failed: set[str] = set()
        self._cached_tickers: list[str] = []
        self._cached_tickers_at: datetime | None = None
        self._next_market_refresh_at: datetime | None = None
        self.last_successful_market_refresh: datetime | None = None
        # Cache of recent /markets payloads keyed by ticker. Refreshed alongside
        # the ticker-list refresh and used to enrich each orderbook snapshot
        # with last trade, volume, open interest, liquidity, status, and close
        # time — needed for both market-making and edge-mining research.
        self._market_payloads: dict[str, dict] = {}
        # High-water mark of trade timestamps per ticker. Used to skip already
        # captured trades when polling /markets/trades each cycle so we do not
        # re-pull a full history every 30 seconds.
        self._last_trade_ts_by_ticker: dict[str, int] = {}
        # Broad all-market collection uses one global /markets/trades poll per
        # cycle instead of one request per ticker. That keeps trade evidence
        # flowing without making all-market coverage rate-limit itself.
        self._last_global_trade_ts: int | None = None
        self._collector_name = "orderbook_recorder"

    def run(
        self,
        market_ticker: str | None = None,
        weather_only: bool = True,
        interval_seconds: int | None = None,
        duration_minutes: int | None = None,
        duration_hours: float | None = None,
        max_markets: int | None = None,
        max_market_pages: int | None = None,
        full_depth: bool | None = None,
        verbose_orderbooks: bool = False,
        record_trades: bool = True,
        batch_orderbooks: bool = True,
        max_global_trade_pages: int = 1,
        universe_priority: str | None = None,
        once: bool = False,
    ) -> OrderbookRecordResult:
        interval_seconds = interval_seconds or self.cfg.orderbook_record_interval_seconds
        max_markets = max_markets or self.cfg.orderbook_record_max_markets
        full_depth = self.cfg.orderbook_record_full_depth if full_depth is None else full_depth
        started_at = datetime.now(timezone.utc)
        if duration_hours is not None and duration_hours > 0:
            deadline = started_at + timedelta(hours=duration_hours)
        elif duration_minutes is not None and duration_minutes > 0:
            deadline = started_at + timedelta(minutes=duration_minutes)
        else:
            deadline = None
        snapshots = 0
        cycles = 0
        markets_seen: set[str] = set()
        warnings: list[str] = []
        last_snapshot_at: datetime | None = None
        last_heartbeat_at: datetime | None = None
        stopped_by_user = False
        scope = "single_market" if market_ticker else (f"universe_{universe_priority}" if universe_priority else ("weather" if weather_only else "all_open"))
        self._collector_name = "orderbook_recorder" if scope == "weather" else f"orderbook_recorder_{scope}"
        self._update_state(
            started_at=started_at,
            last_heartbeat_at=None,
            last_snapshot_at=None,
            cycles=0,
            snapshots=snapshots,
            markets_tracked=0,
            current_task="starting",
            status="STARTING",
            error_message=None,
        )
        LOGGER.info(
            "pure orderbook recorder started scope=%s interval_seconds=%s duration_hours=%s max_markets=%s max_market_pages=%s record_trades=%s batch_orderbooks=%s max_global_trade_pages=%s",
            scope,
            interval_seconds,
            duration_hours,
            max_markets,
            max_market_pages,
            record_trades,
            batch_orderbooks,
            max_global_trade_pages,
        )
        while True:
            try:
                cycles += 1
                self._update_state(
                    started_at=started_at,
                    last_heartbeat_at=last_heartbeat_at,
                    last_snapshot_at=last_snapshot_at,
                    cycles=cycles,
                    snapshots=snapshots,
                    markets_tracked=len(markets_seen),
                    current_task="recording_orderbooks",
                    status="RECORDING",
                    error_message=None,
                )
                if market_ticker:
                    tickers = [market_ticker]
                else:
                    self._update_state(
                        started_at=started_at,
                        last_heartbeat_at=last_heartbeat_at,
                        last_snapshot_at=last_snapshot_at,
                        cycles=cycles,
                        snapshots=snapshots,
                        markets_tracked=len(markets_seen) or len(self._cached_tickers),
                        current_task="market_refresh",
                        status="MARKET_REFRESH",
                        error_message=None,
                    )
                    if universe_priority:
                        tickers = self._universe_tickers(universe_priority, max_markets=max_markets)
                    else:
                        tickers = self._active_tickers(weather_only=weather_only, max_markets=max_markets, max_market_pages=max_market_pages)
                    self._update_state(
                        started_at=started_at,
                        last_heartbeat_at=last_heartbeat_at,
                        last_snapshot_at=last_snapshot_at,
                        cycles=cycles,
                        snapshots=snapshots,
                        markets_tracked=len(tickers),
                        current_task="recording_orderbooks",
                        status="RECORDING",
                        error_message=None,
                    )
                if record_trades and not weather_only and not market_ticker:
                    try:
                        saved_trades = self._record_recent_global_trades(max_pages=max_global_trade_pages)
                        if saved_trades:
                            LOGGER.info("global trade poll saved_or_updated=%s", saved_trades)
                    except Exception as exc:
                        LOGGER.debug("global trade fetch skipped: %s", exc)
                if not weather_only and not market_ticker and batch_orderbooks:
                    rows = self._record_batch(tickers, full_depth=full_depth, interval_seconds=interval_seconds)
                    for row in rows:
                        markets_seen.add(row["market_ticker"])
                        snapshots += 1
                        last_snapshot_at = row["ts"]
                else:
                    per_ticker_trades = record_trades and (weather_only or bool(market_ticker))
                    for ticker in tickers:
                        if ticker in self.closed_or_failed:
                            continue
                        markets_seen.add(ticker)
                        row = self._record_one(
                            ticker,
                            full_depth=full_depth,
                            interval_seconds=interval_seconds,
                            record_trades=per_ticker_trades,
                        )
                        if row:
                            snapshots += 1
                            last_snapshot_at = row["ts"]
                            if verbose_orderbooks or self.cfg.collector_log_every_orderbook:
                                LOGGER.info(
                                    "orderbook %s %s bid=%s ask=%s spread=%s depth_bid=%s depth_ask=%s",
                                    ticker,
                                    row["ts"],
                                    row["yes_best_bid"],
                                    row["yes_best_ask"],
                                    row["spread_cents"],
                                    row["depth_yes_bid_1"],
                                    row["depth_yes_ask_1"],
                                )
                        if self.cfg.kalshi_orderbook_sleep_between_requests_ms > 0:
                            time.sleep(self.cfg.kalshi_orderbook_sleep_between_requests_ms / 1000.0)
            except KeyboardInterrupt:
                stopped_by_user = True
                break
            except Exception as exc:
                message = f"orderbook recorder cycle failed: {exc}"
                LOGGER.warning(message)
                warnings.append(message)
                self._update_state(
                    started_at=started_at,
                    last_heartbeat_at=last_heartbeat_at,
                    last_snapshot_at=last_snapshot_at,
                    cycles=cycles,
                    snapshots=snapshots,
                    markets_tracked=len(markets_seen),
                    current_task="recording_orderbooks",
                    status="DEGRADED_API_ERRORS",
                    error_message=message,
                )
            now = datetime.now(timezone.utc)
            if _heartbeat_due(now, last_heartbeat_at, interval_seconds):
                last_heartbeat_at = now
                self._heartbeat(
                    started_at=started_at,
                    heartbeat_at=now,
                    cycles=cycles,
                    snapshots=snapshots,
                    markets_tracked=len(markets_seen) or len(self._cached_tickers),
                    last_snapshot_at=last_snapshot_at,
                    interval_seconds=interval_seconds,
                    current_task="recording_orderbooks",
                )
            if once:
                break
            if deadline and datetime.now(timezone.utc) >= deadline:
                break
            self._update_state(
                started_at=started_at,
                last_heartbeat_at=last_heartbeat_at,
                last_snapshot_at=last_snapshot_at,
                cycles=cycles,
                snapshots=snapshots,
                markets_tracked=len(markets_seen) or len(self._cached_tickers),
                current_task="sleeping",
                status="SLEEPING",
                error_message=None,
            )
            try:
                time.sleep(max(interval_seconds, 1))
            except KeyboardInterrupt:
                stopped_by_user = True
                break
        finished_at = datetime.now(timezone.utc)
        healthy_recently = last_snapshot_at is not None and (finished_at - last_snapshot_at).total_seconds() <= 600
        status = "STOPPED" if stopped_by_user else "STOPPED"
        self._update_state(
            started_at=started_at,
            last_heartbeat_at=last_heartbeat_at,
            last_snapshot_at=last_snapshot_at,
            cycles=cycles,
            snapshots=snapshots,
            markets_tracked=len(markets_seen) or len(self._cached_tickers),
            current_task="stopped",
            status=status,
            error_message=None,
        )
        LOGGER.info(
            "recorder stopped_by_user=%s runtime_sec=%.1f cycles=%s snapshots_this_run=%s last_snapshot=%s healthy_last_10m=%s",
            stopped_by_user,
            (finished_at - started_at).total_seconds(),
            cycles,
            snapshots,
            last_snapshot_at,
            healthy_recently,
        )
        print(
            "RECORDER SUMMARY "
            f"stopped_by_user={stopped_by_user} runtime_sec={(finished_at - started_at).total_seconds():.1f} "
            f"cycles={cycles} snapshots_this_run={snapshots} last_snapshot={last_snapshot_at} "
            f"healthy_last_10m={healthy_recently}"
        )
        return OrderbookRecordResult(
            started_at=started_at,
            finished_at=finished_at,
            cycles=cycles,
            snapshots=snapshots,
            markets_seen=len(markets_seen),
            last_snapshot_at=last_snapshot_at,
            stopped_by_user=stopped_by_user,
            warnings=warnings[:50],
        )

    def _active_tickers(self, weather_only: bool, max_markets: int, max_market_pages: int | None = None) -> list[str]:
        now = datetime.now(timezone.utc)
        if self._next_market_refresh_at and now < self._next_market_refresh_at and self._cached_tickers:
            LOGGER.info("using cached market list age=%.1fs failure_backoff_active=true", _age_seconds(now, self._cached_tickers_at))
            return self._cached_tickers[:max_markets]
        if self._cached_tickers and self._cached_tickers_at and (now - self._cached_tickers_at).total_seconds() < self.cfg.kalshi_markets_refresh_seconds:
            LOGGER.info("using cached market list age=%.1fs", _age_seconds(now, self._cached_tickers_at))
            return self._cached_tickers[:max_markets]
        try:
            if not weather_only:
                effective_pages = max_market_pages
                if effective_pages is None and max_markets <= 1000:
                    effective_pages = 1
                markets = self.loader.load_active_markets(
                    persist=True,
                    persist_snapshots=False,
                    max_pages=effective_pages,
                    max_markets=max_markets,
                )
            else:
                markets = self.loader.load_active_weather_markets(persist=False, max_pages=1, max_series=max(1, min(max_markets, 25)))
            self._cached_tickers = [str(market.get("ticker")) for market in markets[:max_markets] if market.get("ticker")]
            # Index full market payloads by ticker so each orderbook snapshot
            # can be enriched with last_price/volume/open_interest/liquidity
            # without an extra API call per market each cycle.
            self._market_payloads = {
                str(market.get("ticker")): market for market in markets if market.get("ticker")
            }
            self._cached_tickers_at = now
            self.last_successful_market_refresh = now
            self._next_market_refresh_at = now + timedelta(seconds=self.cfg.kalshi_markets_refresh_seconds)
        except Exception as exc:
            LOGGER.warning("active market refresh failed; continuing with cached markets if available: %s", exc)
            self._next_market_refresh_at = now + timedelta(seconds=self.cfg.kalshi_markets_refresh_failure_backoff_seconds)
        return self._cached_tickers[:max_markets]

    def _universe_tickers(self, universe_priority: str, max_markets: int) -> list[str]:
        priorities = _universe_priorities(universe_priority)
        placeholders = ", ".join(f":p{idx}" for idx, _ in enumerate(priorities))
        params = {f"p{idx}": priority for idx, priority in enumerate(priorities)}
        params["limit"] = max_markets
        frame = self.storage.fetch_sql(
            f"""
            SELECT r.market_ticker, r.score, m.payload
            FROM market_universe_rankings r
            LEFT JOIN markets m ON m.ticker = r.market_ticker
            WHERE r.priority IN ({placeholders})
            ORDER BY r.score DESC, r.recent_trade_count DESC, r.volume_24h DESC
            LIMIT :limit
            """,
            params,
        )
        tickers: list[str] = []
        payloads: dict[str, dict] = {}
        for _, row in frame.iterrows():
            ticker = str(row.get("market_ticker") or "")
            if not ticker:
                continue
            tickers.append(ticker)
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if isinstance(payload, dict):
                payloads[ticker] = payload
        self._cached_tickers = tickers
        self._market_payloads = payloads
        self._cached_tickers_at = datetime.now(timezone.utc)
        self.last_successful_market_refresh = self._cached_tickers_at
        self._next_market_refresh_at = self._cached_tickers_at + timedelta(seconds=self.cfg.kalshi_markets_refresh_seconds)
        return tickers[:max_markets]

    def _record_one(self, ticker: str, full_depth: bool, interval_seconds: int, record_trades: bool = True) -> dict | None:
        depth = 100 if full_depth else 10
        try:
            raw = self.client.get_orderbook(ticker, depth=depth)
        except Exception as exc:
            LOGGER.warning("orderbook fetch failed for %s: %s", ticker, exc)
            self.closed_or_failed.add(ticker)
            return None
        ts = _bucket_time(datetime.now(timezone.utc), interval_seconds)
        row = self._record_orderbook_payload(ticker, raw, ts)
        if record_trades:
            # Best-effort trade capture. Trades are essential for queue-position
            # research (was this fill traded-through or just touched?) and for
            # edge-mining (price-impact / volatility around events). The call is
            # wrapped so a Kalshi outage cannot interrupt orderbook recording.
            try:
                self._record_recent_trades(ticker)
            except Exception as exc:
                LOGGER.debug("trade fetch skipped for %s: %s", ticker, exc)
        return row

    def _record_batch(self, tickers: list[str], full_depth: bool, interval_seconds: int) -> list[dict]:
        rows: list[dict] = []
        active_tickers = [ticker for ticker in tickers if ticker not in self.closed_or_failed]
        for chunk in _chunks(active_tickers, 100):
            try:
                payload = self.client.get_multiple_orderbooks(chunk)
            except Exception as exc:
                LOGGER.warning("batch orderbook fetch failed for %d markets; falling back to single-market calls: %s", len(chunk), exc)
                for ticker in chunk:
                    row = self._record_one(ticker, full_depth=full_depth, interval_seconds=interval_seconds, record_trades=False)
                    if row:
                        rows.append(row)
                continue
            ts = _bucket_time(datetime.now(timezone.utc), interval_seconds)
            for ticker, raw in extract_multi_orderbooks(payload, chunk):
                if not ticker:
                    continue
                try:
                    rows.append(self._record_orderbook_payload(ticker, raw, ts))
                except Exception as exc:
                    LOGGER.debug("batch orderbook normalize failed for %s: %s", ticker, exc)
            if self.cfg.kalshi_orderbook_sleep_between_requests_ms > 0:
                time.sleep(self.cfg.kalshi_orderbook_sleep_between_requests_ms / 1000.0)
        return rows

    def _record_orderbook_payload(self, ticker: str, raw: dict, ts: datetime) -> dict:
        book = NormalizedOrderBook.from_kalshi(ticker, raw)
        market_payload = self._market_payloads.get(ticker)
        row = normalize_live_orderbook_snapshot(ticker, ts, book, raw, market_payload=market_payload)
        self.storage.upsert_live_orderbook_snapshot(row)
        return row

    def _record_recent_trades(self, ticker: str) -> int:
        last_seen = self._last_trade_ts_by_ticker.get(ticker)
        # If we already captured trades for this ticker, only ask for trades
        # newer than the latest one we saved (Kalshi /trades min_ts is
        # second-precision unix). For first-pass tickers, look back one hour
        # so a freshly started recorder still gets context.
        if last_seen is None:
            min_ts = int(datetime.now(timezone.utc).timestamp()) - 3600
        else:
            min_ts = max(0, int(last_seen) - 1)
        try:
            payload = self.client.get_trades(ticker=ticker, min_ts=min_ts, limit=1000)
        except Exception:
            return 0
        trades = payload.get("trades") or []
        if not trades:
            return 0
        saved = 0
        newest_ts = last_seen
        for trade in trades:
            row = _normalize_trade(trade, ticker)
            if not row:
                continue
            try:
                self.storage.upsert_historical_trade(row)
                saved += 1
            except Exception as exc:
                LOGGER.debug("trade upsert failed for %s: %s", ticker, exc)
                continue
            trade_ts = row["ts"]
            if trade_ts is not None:
                ts_int = int(trade_ts.timestamp())
                if newest_ts is None or ts_int > newest_ts:
                    newest_ts = ts_int
        if newest_ts is not None:
            self._last_trade_ts_by_ticker[ticker] = newest_ts
        return saved

    def _record_recent_global_trades(self, max_pages: int = 1) -> int:
        """Capture recent trades across all markets with one paginated poll.

        Broad market-making research needs trade prints for fill evidence, but
        polling `/markets/trades` once per ticker would quickly dominate API
        usage when tracking hundreds or thousands of markets. The global trade
        endpoint gives us a coarse high-water mark that is good enough for
        research ingestion and safely dedupes through `trade_id`.
        """
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if self._last_global_trade_ts is None:
            db_latest = self._latest_trade_ts_from_db()
            db_latest_ts = int(db_latest.timestamp()) if db_latest is not None else None
            min_ts = max(now_ts - 3600, (db_latest_ts - 1) if db_latest_ts is not None else 0)
        else:
            min_ts = max(0, int(self._last_global_trade_ts) - 1)
        saved = 0
        newest_ts = self._last_global_trade_ts
        cursor: str | None = None
        for _ in range(max(max_pages, 1)):
            payload = self.client.get_trades(min_ts=min_ts, limit=1000, cursor=cursor)
            for trade in payload.get("trades") or []:
                ticker = str(trade.get("ticker") or trade.get("market_ticker") or "")
                if not ticker:
                    continue
                row = _normalize_trade(trade, ticker)
                if not row:
                    continue
                try:
                    self.storage.upsert_historical_trade(row)
                    saved += 1
                except Exception as exc:
                    LOGGER.debug("global trade upsert failed for %s: %s", ticker, exc)
                    continue
                trade_ts = row["ts"]
                if trade_ts is not None:
                    ts_int = int(trade_ts.timestamp())
                    if newest_ts is None or ts_int > newest_ts:
                        newest_ts = ts_int
            cursor = payload.get("cursor") or None
            if not cursor:
                break
        if newest_ts is not None:
            self._last_global_trade_ts = newest_ts
        return saved

    def _latest_trade_ts_from_db(self) -> datetime | None:
        try:
            frame = self.storage.fetch_sql("SELECT MAX(ts) AS latest_ts FROM historical_trades")
        except Exception:
            return None
        if frame.empty:
            return None
        value = frame.iloc[0].get("latest_ts")
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _heartbeat(
        self,
        *,
        started_at: datetime,
        heartbeat_at: datetime,
        cycles: int,
        snapshots: int,
        markets_tracked: int,
        last_snapshot_at: datetime | None,
        interval_seconds: int,
        current_task: str,
    ) -> None:
        last_age = (heartbeat_at - last_snapshot_at).total_seconds() if last_snapshot_at else None
        db_count = _db_snapshot_count(self.storage)
        rate_limits = int(self.client.stats.get("total_429s", 0))
        market_cache = "active"
        if self._cached_tickers_at and heartbeat_at < (self._next_market_refresh_at or heartbeat_at):
            market_cache = f"cached age={_age_seconds(heartbeat_at, self._cached_tickers_at):.0f}s"
        line = (
            f"HEARTBEAT recorder alive local_time={heartbeat_at.astimezone().isoformat(timespec='seconds')} "
            f"cycles={cycles} snapshots_this_run={snapshots} db_snapshots={db_count} markets={markets_tracked} "
            f"last_snapshot_age_sec={last_age if last_age is not None else 'none'} rate_limits={rate_limits} "
            f"current_task={current_task} market_refresh={market_cache}"
        )
        LOGGER.info(line)
        print(line, flush=True)
        status = "RECORDING"
        if last_age is None or last_age > max(interval_seconds * 2, 1):
            status = "DEGRADED_API_ERRORS"
            LOGGER.warning("No snapshot written in more than two intervals; last_snapshot_age_sec=%s", last_age)
        if last_age is None or last_age > 600:
            status = "ERROR"
            LOGGER.error("No snapshot written in more than 10 minutes; recorder will keep trying.")
        self._update_state(
            started_at=started_at,
            last_heartbeat_at=heartbeat_at,
            last_snapshot_at=last_snapshot_at,
            cycles=cycles,
            snapshots=snapshots,
            markets_tracked=markets_tracked,
            current_task=current_task,
            status=status,
            error_message=None,
        )

    def _update_state(
        self,
        *,
        started_at: datetime,
        last_heartbeat_at: datetime | None,
        last_snapshot_at: datetime | None,
        cycles: int,
        snapshots: int,
        markets_tracked: int,
        current_task: str,
        status: str,
        error_message: str | None,
    ) -> None:
        try:
            self.storage.upsert_collector_state(
                {
                    "collector_name": self._collector_name,
                    "started_at": started_at,
                    "last_heartbeat_at": last_heartbeat_at,
                    "last_snapshot_at": last_snapshot_at,
                    "cycles_completed": cycles,
                    "snapshots_this_run": snapshots,
                    "markets_tracked": markets_tracked,
                    "current_task": current_task,
                    "status": status,
                    "error_message": error_message,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        except Exception as exc:
            LOGGER.debug("collector_state update failed: %s", exc)


def normalize_live_orderbook_snapshot(
    ticker: str,
    ts: datetime,
    book: NormalizedOrderBook,
    raw: dict,
    market_payload: dict | None = None,
) -> dict:
    yes_bids = [{"price_cents": level.price_cents, "size": level.size} for level in book.yes_bids]
    no_bids = [{"price_cents": level.price_cents, "size": level.size} for level in book.no_bids]
    yes_best_bid = book.yes_bid
    yes_best_ask = book.yes_ask
    no_best_bid = book.no_bid
    no_best_ask = book.no_ask
    market_payload = market_payload or {}
    last_price = _market_price_cents(
        market_payload.get("last_price_dollars"),
        market_payload.get("last_price"),
    )
    prev_yes_bid = _market_price_cents(
        market_payload.get("previous_yes_bid_dollars"),
        market_payload.get("previous_yes_bid"),
    )
    prev_yes_ask = _market_price_cents(
        market_payload.get("previous_yes_ask_dollars"),
        market_payload.get("previous_yes_ask"),
    )
    liquidity_cents = _market_price_cents(
        market_payload.get("liquidity_dollars"),
        market_payload.get("liquidity"),
    )
    return {
        "market_ticker": ticker,
        "ts": ts,
        "yes_bids_json": json.dumps(yes_bids),
        "no_bids_json": json.dumps(no_bids),
        "yes_best_bid": yes_best_bid,
        "yes_best_ask": yes_best_ask,
        "no_best_bid": no_best_bid,
        "no_best_ask": no_best_ask,
        "spread_cents": yes_best_ask - yes_best_bid if yes_best_bid is not None and yes_best_ask is not None else None,
        "mid_cents": (yes_best_bid + yes_best_ask) / 2 if yes_best_bid is not None and yes_best_ask is not None else None,
        "depth_yes_bid_1": book.depth_at_best_bid,
        "depth_yes_ask_1": book.depth_at_best_ask,
        "depth_no_bid_1": book.depth_at_best_ask,
        "depth_no_ask_1": book.depth_at_best_bid,
        "total_yes_bid_depth": sum(level.size for level in book.yes_bids),
        "total_no_bid_depth": sum(level.size for level in book.no_bids),
        "last_price_cents": last_price,
        "previous_yes_bid_cents": prev_yes_bid,
        "previous_yes_ask_cents": prev_yes_ask,
        "volume": _market_volume(market_payload.get("volume_fp"), market_payload.get("volume")),
        "volume_24h": _market_volume(market_payload.get("volume_24h_fp"), market_payload.get("volume_24h")),
        "open_interest": _market_volume(market_payload.get("open_interest_fp"), market_payload.get("open_interest")),
        "liquidity_cents": liquidity_cents,
        "market_status": str(market_payload.get("status") or "") or None,
        "market_close_time": _parse_market_dt(market_payload.get("close_time")),
        "raw_json": json.dumps(raw, default=str),
        "source": "kalshi_current_orderbook",
    }


def _market_price_cents(dollars: object, cents: object) -> float | None:
    """Return a Kalshi market price field as cents, accepting either string
    dollar-denominated values (``"0.42"``) or already-cent integers (``42``)."""
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


def _bucket_time(timestamp: datetime, interval_seconds: int) -> datetime:
    epoch = int(timestamp.timestamp())
    bucket = epoch - (epoch % max(interval_seconds, 1))
    return datetime.fromtimestamp(bucket, tz=timezone.utc)


def _heartbeat_due(now: datetime, last_heartbeat_at: datetime | None, interval_seconds: int) -> bool:
    if last_heartbeat_at is None:
        return True
    heartbeat_seconds = max(60, min(300, interval_seconds * 4))
    return (now - last_heartbeat_at).total_seconds() >= heartbeat_seconds


def _db_snapshot_count(storage: Storage) -> int | None:
    try:
        frame = storage.fetch_sql("SELECT COUNT(*) AS count FROM orderbook_snapshots_live")
        return int(frame.iloc[0]["count"]) if not frame.empty else 0
    except Exception:
        return None


def extract_multi_orderbooks(payload: dict, requested_tickers: list[str]) -> list[tuple[str, dict]]:
    """Normalize Kalshi's multi-orderbook response into `(ticker, book)` pairs."""
    raw_entries = (
        payload.get("orderbooks")
        or payload.get("market_orderbooks")
        or payload.get("markets")
        or payload.get("data")
        or []
    )
    if isinstance(raw_entries, dict):
        rows: list[tuple[str, dict]] = []
        for ticker, entry in raw_entries.items():
            if not isinstance(entry, dict):
                continue
            rows.append((str(ticker), _entry_orderbook_payload(entry)))
        return rows
    if not isinstance(raw_entries, list):
        return []
    rows = []
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            continue
        ticker = (
            entry.get("ticker")
            or entry.get("market_ticker")
            or entry.get("market")
            or (requested_tickers[idx] if idx < len(requested_tickers) else None)
        )
        if not ticker:
            continue
        rows.append((str(ticker), _entry_orderbook_payload(entry)))
    return rows


def _entry_orderbook_payload(entry: dict) -> dict:
    for key in ("orderbook_fp", "orderbook"):
        node = entry.get(key)
        if isinstance(node, dict):
            return node
    return entry


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[start : start + size] for start in range(0, len(items), max(size, 1))]


def _universe_priorities(name: str) -> list[str]:
    normalized = name.strip().lower().replace("-", "_")
    if normalized in {"high", "high_priority"}:
        return ["RECORD_HIGH_PRIORITY"]
    if normalized in {"medium", "high_medium", "priority"}:
        return ["RECORD_HIGH_PRIORITY", "RECORD_MEDIUM_PRIORITY"]
    if normalized in {"recordable", "usable", "all_priority"}:
        return ["RECORD_HIGH_PRIORITY", "RECORD_MEDIUM_PRIORITY", "RECORD_LOW_PRIORITY"]
    if normalized in {"all", "any"}:
        return ["RECORD_HIGH_PRIORITY", "RECORD_MEDIUM_PRIORITY", "RECORD_LOW_PRIORITY", "METADATA_ONLY"]
    return [name]


def _age_seconds(now: datetime, then: datetime | None) -> float:
    if then is None:
        return -1.0
    return (now - then).total_seconds()
