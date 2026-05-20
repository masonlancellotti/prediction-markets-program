# Project Purpose

## Mission

This repo is a Kalshi-only prediction-market research system. The first fair-value edge track is weather, especially daily temperature markets, because weather has external settlement data we can model. A second, broader track now collects all open Kalshi markets for liquidity/market-making research. The goal is to find real, tradable edge as quickly as possible while refusing comforting fake P&L.

Live trading is disabled until conservative backtests and paper trading justify tiny real-money testing. The system should help decide: trade tiny in paper, keep collecting data, change the strategy, or abandon the idea.

## Core Philosophy

Think like a market maker and risk manager, not a gambler.

- No midpoint-fill fantasy.
- No edge claims without fees, spreads, slippage, fills, settlement truth, and robustness checks.
- Positive final-settlement P&L is not enough by itself; weather-mined signals also need future-price confirmation so we know the entry timing was tradable, not just eventually right or lucky.
- Historical full L2 Kalshi orderbooks are likely unavailable, so recorded live orderbooks are a core asset across weather and non-weather markets.
- Exact NWS Daily Climate Report settlement labels matter because hourly ASOS/METAR observations can differ from official reports.
- Parser and settlement correctness are edge-protection gates. Range/bucket contracts must not be treated as simple thresholds.
- If no edge exists, the correct output is: "No reliable edge found yet."

## Current Research Targets

- Already-hit threshold lag or stale pricing.
- Late-day high-temperature fade.
- Ladder consistency and monotonicity violations.
- Wide-spread passive market-making across all Kalshi categories.
- Future passive replay using recorded full orderbooks.

There are two separate edge families:

- Fair-value edge: estimate contract probability from as-of domain data and compare it to executable bid/ask after fees and uncertainty buffers. Weather is the only mature fair-value domain right now.
- Weather mining currently has one best hypothesis to monitor: range-bucket BUY_NO dislocations. It is not paper-ready until new dates preserve net P&L and improve future-mid confirmation.
- Liquidity edge: quote passively in wide spreads only if conservative fill evidence and adverse-selection diagnostics say fills are not mostly bad fills. This can screen all open Kalshi markets before custom domain parsers exist.
- Market-making replay: use `backtest-market-making` to replay the paper-maker loop over recorded data and choose better forward paper targets faster.
- Paper market-making bridge: once `analyze-market-making` finds a `PAPER_WATCHLIST` row, `paper-market-making` tracks one tiny simulated passive quote strategy live against recorded books/trades. This is the fastest current path toward possible small real-money testing, but it is still paper-only evidence collection.
- Paper market-making basket: when one-market paper tracking is too quiet, `paper-market-making-basket` runs several tiny paper-only trackers at once to gather fill evidence faster without weakening live-trading gates.
- Market universe selection: broad discovery should first identify markets with two-sided books, depth, spreads, and trade activity. Do not spend recorder budget equally across empty or one-sided markets. `KXMVE...` multivariate/combinatoric tickers are excluded from universe priority by default because recent scans showed many had occasional trades but empty/current one-sided orderbooks.

These must not be combined into one vague P&L number.

## Non-Goals For Now

- Live automated trading.
- Twitter, news, or sentiment bots.
- Deep learning.
- Beautiful UI polish.
- Overfit parameter mining.
- Any strategy that cannot be explained trade by trade.
- Live all-category fair-value models before each category has a real data source, parser, and backtest.

## Decision Standard

A strategy is not promising unless it:

- Uses correct contract parsing.
- Uses high-confidence settlement labels.
- Produces positive net P&L after fees.
- Survives worse fills.
- Is not driven by one outlier.
- Has enough sample size.
- Beats future or closing prices in signal tests.
- For passive liquidity, shows fills beat future prices and do not rely on touched-only fills.
- For paper market-making, produces repeated paper fills with positive mark/future-mid edge after fees before any real-money discussion.
- Can be explained clearly.

## Naming / Location Note

The user plans to move this repo under a broader prediction-market workspace and rename it from `kalshi-weather-edge` to `kalshi-fair-value-and-liquidity-edge`, reflecting the current two-prong scope: weather fair-value research plus Kalshi liquidity/market-making research. Scope remains Kalshi-only until explicitly changed.
