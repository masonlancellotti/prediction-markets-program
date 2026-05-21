# Project Purpose

## What This Is

`relative-value-scanner` is a read-only research scaffold for comparing prediction-market prices across venues and against sportsbook reference odds.

## What This Is Not

- Not a trading bot.
- Not an account-connected app.
- Not a Robinhood, Polymarket, IBKR, or Kalshi execution system.
- Not proof of arbitrage unless strict matching, settlement, liquidity, fee, and executability gates pass.

## Platform Priority

1. Kalshi and other prediction-market exchange quotes as executable research candidates.
2. Polymarket-style exchange quotes as read-only comparison candidates.
3. Reference-only sources, including sportsbook odds, only as non-executable `WATCH`/diagnostic inputs.
4. Signal-only sources, including planned Manifold and Metaculus support, only for discovery and semantic clustering.

Weather remains the only proprietary edge domain for now because it has external observations, forecasts, settlement labels, and replay data. This repo remains infrastructure for matching, normalization, reference comparison, source taxonomy, and fake-edge prevention.

## Long-Term Goal

Build a conservative scanner that can surface cross-venue relative-value candidates without overclaiming, then graduate only verified paper signals into deeper venue-specific research.
