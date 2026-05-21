# Data Reality

## Exchange Quotes

Exchange quotes can indicate executable market prices only if the venue adapter is authenticated/read-capable and the market is live. This first scaffold uses fixture data only, so it proves logic, not live tradability.

Adapters must normalize top-of-book size into `liquidity_top_contracts`, measured in contracts. Do not pass raw USD, USDC, or venue notional into this field.

## Polymarket Live Discovery

`python scan.py fetch-polymarket` uses Polymarket's public Gamma discovery API for active, not-closed events and markets. Normalized rows are filtered by default to exclude closed, archived, inactive, not-accepting-orders-or-unknown, and clearly past-end-date markets; the raw response remains in the snapshot for audit. It does not authenticate, connect a wallet, call trading endpoints, place orders, or score live markets against Kalshi.

Optional `--tag-slug` and `--tag-id` arguments target the same public Gamma discovery endpoint by tag. They are discovery filters only; when omitted, the broad active/not-closed request remains unchanged.

Normalized outcome rows use `outcome_yes_token_price`, meaning the Gamma-discovered price of the YES token for that specific outcome. Market-level `best_bid` and `best_ask` are also Gamma discovery numbers when present; they are not normalized orderbook depth and should not be treated as executable liquidity proof. `enableOrderBook` is recorded as a discovery field only. Skip counters can overlap, so their sum is not `market_count - normalized_count`.

## Kalshi Live Discovery

`python scan.py fetch-kalshi` uses Kalshi's public read-only `GET /markets?status=open` endpoint. It does not authenticate, read account state, call order endpoints, place orders, or score live markets against Polymarket. Normalized rows are filtered by default to exclude closed, settled, expired, inactive, and clearly past-close-time markets. Kalshi currently returns some open-query markets with `status=active`; this is treated as live/open for discovery.

Optional `--series-ticker` and `--event-ticker` arguments target the same public markets endpoint by Kalshi series or event. Optional `--cursor` starts from a returned page cursor, and `--max-pages` follows returned `cursor` or `next_cursor` values. When omitted, the broad one-page `status=open&limit=...` request remains unchanged.

Kalshi normalized snapshots use `schema_version=1`. For early-closing markets with `can_close_early=true` and a parseable `expected_expiration_time`, normalized `end_date` uses `expected_expiration_time` as the Polymarket-comparable date while normalized `close_time` preserves the conservative close/expiration fallback. Outcome rows use `outcome_yes_token_price` for the displayed outcome token price or closest venue equivalent. Market-level `best_bid` and `best_ask` are YES bid/ask discovery values from market metadata, not proof of orderbook depth. `liquidity` is Kalshi's liquidity-dollar field when present, not `liquidity_top_contracts`.

## Snapshot Schema Contract

Live discovery snapshots use `schema_version=1` for both Polymarket and Kalshi. Common top-level fields are `schema_version`, `source`, `captured_at`, `event_count` when available, `market_count`, `normalized_count`, and `normalized_markets`.

Common normalized market fields, when available, are `venue`, `event_id`, `event_title`, `market_id` or `ticker`, `question` or `title`, `outcomes`, `best_bid`, `best_ask`, `volume`, `liquidity`, `end_date` or `close_time`, `active`, `closed`, `status`, and `raw`.

Venue-specific fields are allowed for audit/debugging, but consumers must rely only on documented common fields unless they explicitly branch on a venue-specific field. These snapshots remain discovery-only and are not live scoring inputs.

## Live Snapshot Matching

`python scan.py match-live-snapshots` reads saved schema-v1 snapshot JSON files from disk and emits tentative market pairs for manual review only. It uses text overlap on `event_title` and `question`/`title`, plus small matching aids from timezone-aware close/end-time proximity and shared event/league keywords.

Deadline closeness and shared tokens such as NBA, election, BTC, CPI, or Fed are review aids only. They do not prove settlement-rule equivalence, executable liquidity, fee-adjusted value, or arb.

Sports futures also require competition-scope equivalence. ALCS, NLCS, ALDS, NLDS, AFC/NFC championship, conference final(s), division series, semifinal, wild card, conference, league, Champions League group stage, Champions League round of 16, or Copa America group stage markets are not equivalent to overall championship markets such as World Series, Super Bowl, Stanley Cup, World Cup, MLS Cup, NBA Finals, NHL Finals, Premier League title, La Liga title, Bundesliga title, Serie A title, Champions League title, Copa America, or Euro Championship, and city/team aliases such as Dodgers/LAD versus Angels/LAA/Los Angeles A are handled conservatively.

This path does not call live APIs, does not use `RelativeValueScanner`, does not produce `PAPER`, `PAPER_CANDIDATE`, or `POSSIBLE_ARB`, and does not claim executable liquidity. `liquidity` and `volume` remain venue metadata until units and orderbook depth are explicitly normalized.

## Read-Only Orderbook Enrichment

`python scan.py enrich-orderbooks` reads saved schema-v1 snapshots and writes enriched saved JSON. It uses public read-only Kalshi orderbook and Polymarket CLOB book endpoints, never authenticates, never reads account state, and never places or cancels orders.

Kalshi depth is normalized into YES-price space: YES bids remain bids, while NO bids imply YES asks as `1 - no_bid`. Polymarket depth is token-specific; rows without an unambiguous YES token id remain `unenriched` rather than guessing.

Orderbook enrichment records best bid/ask, spread, depth-at-best, depth within 1c/3c/5c, endpoint, status, and warnings. These fields are current read-only book observations only. They are not scored, not matched, not proof of executable liquidity, and cannot produce `POSSIBLE_ARB`.

## Paper Candidate Evaluation

`python scan.py evaluate-paper-candidates` reads saved JSON only: matched snapshot pairs plus Kalshi and Polymarket enriched snapshots. It does not call APIs, authenticate, read accounts, place orders, score through `RelativeValueScanner`, or write anything except the requested ledger JSON.

The evaluator uses bid/ask only and never midpoint. It requires enriched orderbooks, fresh timezone-aware `orderbook_captured_at` values, non-null top-of-book bid/ask, minimum depth on the side that would be hit, bounded settlement-time deltas, propagated matcher warnings, and per-leg fee subtraction. For settlement-time comparison it prefers normalized `end_date`, then falls back to `close_time` only when `end_date` is missing; present but naive or unparseable `end_date` values fail safely.

Fee models are venue-specific in this path: Polymarket currently uses no-fee comparison as an explicit placeholder, while Kalshi uses the conservative tiered estimate. This prevents accidentally applying Kalshi's fee model to the Polymarket leg.

Polymarket shares and Kalshi contracts are not unit-normalized. The ledger always records `polymarket_shares_vs_kalshi_contracts_not_normalized`; without `--accept-unit-mismatch`, this caps otherwise clean rows at `MANUAL_REVIEW`.

Markout windows are placeholders only. Null `t_plus_30s`, `t_plus_5m`, `t_plus_30m`, and `t_plus_2h` fields are not evidence. A future saved-snapshot markout pass must fill them before any paper result is interpreted.

## Markout Replay

`python scan.py replay-paper-candidate-markouts` reads a saved paper candidate ledger and later saved enriched snapshots. It does not call APIs, authenticate, read accounts, place orders, score through `RelativeValueScanner`, or alter any `POSSIBLE_ARB` gate.

Replay joins rows by Polymarket `market_id` and Kalshi `ticker`, then uses the original ledger's `BUY_YES`/`SELL_YES` direction. `BUY_YES` uses later best ask and `SELL_YES` uses later best bid. It never uses midpoint prices, never assumes a fill, and never walks the book.

A filled markout is research evidence that a later saved quote was observed near the requested window. It is not guaranteed profit, not proof of executable liquidity, and not settlement-rule equivalence. If later quotes are missing, stale, too early, or too late, values stay null and `markout_status` explains why.

## Targeted Pipeline Runner

`python scan.py run-targeted-pipeline` is a repeatable orchestration wrapper around the saved-file workflow for one target universe. It runs read-only Polymarket discovery, read-only Kalshi discovery, saved snapshot orderbook enrichment, saved snapshot matching, and saved-file paper candidate evaluation into labeled report files.

The runner does not sleep or wait for later markouts. It prints the exact markout replay command to run after separate later snapshots have been captured. It does not trade, authenticate, read accounts, place orders, score through `RelativeValueScanner`, use midpoint fills, make profit claims, or emit `PAPER` / `POSSIBLE_ARB`.

The runner forwards evaluator review flags such as `--max-settlement-delta-seconds`, `--min-net-gap`, `--min-top-of-book-size`, and `--accept-unit-mismatch`. These are pass-through controls only; evaluator defaults and settlement-gate logic are unchanged.

## Sportsbook Odds

Sportsbook prices are reference prices only. No-vig conversion removes listed overround, but it does not make the sportsbook leg executable inside this scanner. A sportsbook/reference pair can never be `POSSIBLE_ARB`.

## Settlement Risk

Similar event names are not enough. Missing settlement dates cap match confidence. Conflicting settlement dates create high mismatch risk and cap action severity. Different settlement rules also increase mismatch risk.

## Fee and NO-Side Assumptions

Exchange-vs-exchange gaps are reduced by per-leg fee estimates and a conservative NO-side spread penalty when the scanner uses the opposite venue's YES bid as an assumed NO-side offset. This prevents marginal top-of-book gaps from being promoted as executable arb.

## Stale Data

Fixture data is static and should not be treated as fresh. Future live adapters must include timestamps and freshness checks before any `PAPER` or `POSSIBLE_ARB` output is trusted.

`settlement_time` and `captured_at` must be timezone-aware when present. Naive datetimes are rejected because they can create false freshness and settlement alignment.

## Current Limitations

- Network data is limited to explicit read-only Polymarket Gamma and Kalshi market discovery snapshots.
- Polymarket snapshots are discovery-only; filtered markets are more useful than raw Gamma rows but are not yet proof of executable liquidity.
- Kalshi snapshots are discovery-only; filtered markets are not yet matched against Polymarket and are not proof of executable depth.
- Targeted discovery helps pull overlapping live universes, but it does not prove semantic equivalence, settlement alignment, executable liquidity, or edge.
- Orderbook enrichment is saved-file-only plus read-only book lookup; it is not integrated into scoring or live matching.
- Paper candidate evaluation ends at `PAPER_CANDIDATE`; it is not live trading, not position tracking, not P&L, and not markout proof.
- Markout replay is saved-file-only research evidence; it is not live trading, P&L, fill simulation, or proof that an opportunity was executable.
- Targeted pipeline runner is convenience orchestration only; it does not change any safety gate or convert research outputs into trading outputs.
- No real API keys.
- No database.
- No scheduler.
- Live cross-venue matching is saved-file-only and emits review pairs only.
- No settlement history validation.
- No actual NO-side orderbook depth.
- Fee models are conservative placeholders until venue-specific schedules are verified.
- Fixture timestamps are deterministic scaffolding, not real-time freshness proof.
