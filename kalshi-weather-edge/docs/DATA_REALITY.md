# Data Reality

## Kalshi Data

- Current live orderbooks can be recorded going forward for weather and non-weather markets.
- Current live orderbooks are the scarce data priority. Keep `record-orderbooks` pure and isolated.
- `record-orderbooks --all-markets` is for broad market-making/liquidity research only. It records open Kalshi markets across categories but does not provide category-specific fair value, settlement truth, or strategy approval.
- `load-markets --all-markets` persists raw market metadata for future category parsers without trying to parse non-weather contracts today.
- `rank-market-universe` is the preferred gate before expanding broad collection. It separates open markets from useful recorder targets by current two-sided book quality, spread/depth, recent local snapshots, recent trades, and metadata activity. Use it to avoid wasting recorder budget on empty or one-sided markets.
- When recorders are actively writing SQLite, `rank-market-universe` may need `--skip-local-stats --no-persist` for a live diagnostic probe. To update DB-backed rankings for `record-orderbooks --from-universe`, pause recorders briefly so SQLite writes can complete.
- New live orderbook snapshots can include market-state context from Kalshi `/markets` payloads (`last_price_cents`, previous bid/ask, volume, open interest, liquidity, status, close time). Old snapshots before this schema change have nulls in those columns.
- The live orderbook recorder also polls recent trades and persists them into `historical_trades` with `trade_id` dedupe when Kalshi supplies IDs. Weather-only recording polls per ticker; all-market recording uses a bounded global trade poll so trade capture cannot block orderbook breadth. Old rows may lack `trade_id`.
- Market-making research can use `orderbook_snapshots_live` plus `historical_trades` before final weather settlements exist. This is faster than fair-value P&L research, but it still cannot prove queue position or guarantee fills.
- Historical full L2 orderbooks are likely unavailable publicly; broad market-making work therefore depends on recording live orderbooks now.
- Historical candlesticks and trades can support conservative taker and signal tests.
- Passive maker tests from historical candlesticks/trades are approximate.
- Recorded live full orderbooks improve passive replay, but still do not reveal true queue position.
- Wide spreads are not free money. A fill can mean adverse selection: the quote gets hit because fair value just moved against us.
- Touched-only passive quotes should be treated as no-fill by default.
- Traded-through fills are stronger evidence, but still approximate without queue position and exact trade prints.
- Actual Kalshi trade prints are now the preferred passive-fill evidence. A passive quote candidate is only interesting if trade-print fills beat future mids after adverse-selection penalties, not merely because the spread was wide.
- `paper-market-making` is the paper bridge for liquidity edge. It still has queue uncertainty, but it is stricter than touched-only replay: simulated quotes fill only when recorded trade prints trade through the paper limit before the quote TTL. Use it to decide whether an analyzer candidate deserves more paper time, not to claim live profitability.
- `backtest-market-making` replays the same paper-maker idea over recorded books/trades so candidate selection is faster. It is useful for ranking markets, but still cannot prove queue priority, cancel latency, or live fill probability.
- Non-weather fair-value edge is not available just because we record non-weather orderbooks. Each category needs its own settlement source, as-of feature source, parser, and replay before directional edge claims are allowed.

## Weather Data

- Kalshi weather markets often settle using official NWS Daily Climate Report / Climatological Report products.
- Exact NWS reports should be preferred for primary settlement labels.
- Hourly station observations and IEM ASOS are useful fallback labels, but they are imperfect.
- Open-Meteo and reanalysis data must not be used as settlement truth.
- Settlement labels below the confidence threshold should not be used in primary P&L.
- Live station observations can be recorded separately with `record-weather-observations`. They improve as-of features but are still not final settlement truth.
- Live NWS observation length units must be honored. NWS commonly returns precipitation as `wmoUnit:mm`, not meters. Code now converts by unit, but historical live precipitation rows recorded before 2026-05-16 may be 1000x too large and should be repaired or excluded before precipitation-aware analysis.
- Live forecast snapshots can be recorded separately with `record-weather-forecasts`. They are fragile because historical forecast snapshots may not be reconstructable later.
- Weather recorders may fail or time out without stopping orderbook collection. That separation is intentional.
- Exact final settlement labels are still built separately from NWS Daily Climate Reports when available.
- NWS Daily Climate Report issuance timestamps are parsed again; this metadata matters when choosing between same-date report candidates.
- Recorded replay now promotes richer weather features from recorded live observations/forecasts under no-lookahead constraints: month, day-of-year, season, dewpoint, humidity, wind, pressure, visibility, current precip, accumulated same-day precip, forecast dewpoint/humidity/wind, forecast precipitation probability, forecast QPF, and sky cover.
- Replay ignores implausible `precip_1h > 5 inches` values when computing same-day precipitation accumulation and marks `precip_data_warning`, because old pre-fix NWS precipitation rows may be unit-corrupted.
- Weather mining can now isolate slices such as `--target range-bucket-buy-no`, but positive final-settlement P&L alone is not enough. The 2026-05-15 to 2026-05-16 focused run was positive after fees but failed future-mid confirmation, so it stays research-only until new dates show better behavior.

## Contract Semantics

Range/bucket markets are dangerous if parsed incorrectly. A market saying `66-67 degrees` means the final high or low must land inside that bucket. It is not equivalent to `<66`, `<=67`, `>66`, or `>=66`.

Current parser and settlement version: `v2_range_bucket_semantics`.

Old P&L generated before this version is stale unless explicitly regenerated and labeled non-stale.

## No-Lookahead Rules

- Features at timestamp T can only use weather observations at or before T.
- Final settlement can only be used for final labels and payout.
- Future observations, final highs/lows, and forecast revisions cannot leak into replay features.
- Recorded forecasts are usable at replay timestamp T only if `ts_recorded <= T`.
- Recorded live observations are usable at replay timestamp T only if `ts_recorded <= T` and the observation time is not in the future.

## Known Data Risks

- Station mapping errors.
- Range/bucket market parser errors.
- Settlement source metadata inconsistencies.
- Missing NWS climate reports for some stations/dates.
- Missing recorded forecast snapshots for late-day strategy windows.
- Low-confidence active market-to-station mappings, especially cities where Kalshi may use a different official station than the obvious airport/city code.
- Sparse orderbook snapshots.
- Category coverage gaps from API caps, rate limits, and process downtime.
- Raw all-market counts can be huge because Kalshi exposes many empty/one-sided combinatoric markets. Prioritize `two_sided_markets`, `candidate_markets`, `filled_markets`, and `market_universe_rankings.priority` over raw market count.
- `KXMVE...` multivariate/combinatoric markets can show occasional trades while the batch orderbook is empty. They are excluded from universe probe budget and recorder priority by default because they waste market-making collection slots. Use `rank-market-universe --include-multivariate` only when deliberately diagnosing that family.
- Weather replay is a weather-only process. After all-market recording, raw recorded ticker counts can exceed 190k, but only parsed weather contracts should enter `build-recorded-replay`. Use `--recorded-weather-only` for fast smoke tests when external historical NWS fallback is not needed.
- Low liquidity and fake tradability.
- Duplicate stale backtest runs.
- Historical duplicate `parsed_contracts` rows: new identical writes are skipped, but existing duplicates still need one-shot cleanup.
- Historical precipitation values before the NWS unit fix may be mis-scaled.
- Existing recorded replay rows must be rebuilt before richer weather columns are available for analysis.
- Old runs generated with old parser or settlement semantics.

## Trust Levels

- Conservative taker backtests with high-confidence labels: most trustworthy.
- Recorded live as-of weather/forecast replay: better for model tests than reconstructed weather when available.
- Signal-only tests: useful, but not P&L.
- Passive maker results without recorded full books: approximate.
- Passive maker results with recorded full books: better, but queue-uncertain.
- Passive liquidity research is only interesting when fills beat future prices after 5/15/30/60 minutes, not merely when final settlement works out.
- Weather fair-value mining also needs future-price confirmation. Final settlement can be right while entry timing is bad enough that paper/live execution would be poor.
- Trade-print-based market-making research is useful for early screening, but paper/live readiness still requires enough fills, low adverse-selection rate, and manual review.
- Paper market-making logs are stronger than offline analyzer rows because they run forward from the current state, but they still do not prove queue priority or live executability. Treat them as the next gate, not the final gate.
- Market-making replay/backtest is stronger than simple spread scans because it uses trade-print fills and future mids, but weaker than forward paper logs because it is still replayed and queue-unknown.
- As of 2026-05-19, `analyze-market-making` reports `trades=` only for markets with analyzable two-sided book rows in the window. This is intentional: it is the relevant trade-print evidence for market-making candidates, not a full Kalshi tape count.
- `paper-market-making-basket` widens paper-only evidence collection across several targets. It is useful when one market is quiet, but exploratory basket targets are not proof of edge. Only trade-print fills with favorable future markouts after fees should move a target toward paper confidence.
- Any result from stale parser/settlement versions: invalid for edge decisions.
