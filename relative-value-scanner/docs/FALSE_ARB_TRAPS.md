# False Arb Traps

## Opposite Outcomes

Markets that look textually similar can refer to opposite YES sides, such as over vs under or will vs will not. The scanner mitigates this today by detecting comparator polarity and capping match confidence when polarity conflicts. What is still uncovered: nuanced phrasing that implies opposition without explicit comparator words.

## Numeric-Threshold Drift

Two markets can share teams and event names but differ by one threshold, such as over 91.5 vs over 101.5. The scanner mitigates this today by extracting numeric tokens and capping similarity when thresholds differ. What is still uncovered: thresholds written as words or embedded in uncommon notation.

## Settlement-Date Drift

Markets can refer to similar events but settle on different dates or at different cutoff times. The scanner mitigates this today by raising mismatch risk and capping confidence when settlement times are missing or far apart. What is still uncovered: venue-specific settlement timing not present in adapter data.

## Settlement-Rule Drift

Venue rules can differ even when market names match. The scanner mitigates this today by requiring key settlement-rule token compatibility and adding `side_definition_unverified` when rules are empty or materially different. What is still uncovered: legalistic rule differences that share the same high-level keywords.

## NO-Side Spread

Using a YES bid as the implied cost of the opposite NO side can overstate executable edge. The scanner mitigates this today by subtracting `no_side_spread_penalty` from exchange-vs-exchange fee-adjusted gaps and tagging `no_side_spread_assumed`. What is still uncovered: actual NO book depth and slippage until adapters provide it.

## USD vs USDC Currency

Cross-venue comparisons can mix cash, stablecoin, and withdrawal/bridge risk. The scanner mitigates this today by refusing to label sportsbook/reference pairs as executable and by keeping `POSSIBLE_ARB` gated to exchange-vs-exchange candidates. What is still uncovered: currency conversion, funding, withdrawal, and stablecoin depeg risk.

## Fee Underestimation

Small apparent edges can vanish after fees. The scanner mitigates this today with a `FeeModel` interface and a conservative default `KalshiTieredFeeModel` that computes fees per executed leg. What is still uncovered: venue-specific fee schedules beyond the current conservative placeholders.

## Depth and Slippage

Displayed top-of-book prices may not fill enough size. The scanner mitigates this today by requiring limiting `liquidity_top_contracts` for `POSSIBLE_ARB`. What is still uncovered: order queue priority, partial fills, hidden liquidity, and impact beyond fixture depth.

## Side-Definition Divergence

The same phrase can map to different official YES definitions across venues. The scanner mitigates this today by treating missing or incompatible settlement-rule tokens as `side_definition_unverified` and capping confidence. What is still uncovered: semantic rule conflicts that need human review or formal parsers.

## Stale Quotes

A stale quote can create fake spread. The scanner mitigates this today by requiring timezone-aware `captured_at` when present, comparing pair freshness, tagging `stale_quote`, and capping stale pairs at `MANUAL_REVIEW`. What is still uncovered: live adapter clock skew, venue latency, and quote freshness beyond fixture timestamps.

## Lexical Polarity Gap

Sports and event phrases use many verbs for opposite sides, such as wins vs loses or passes vs fails. The scanner mitigates this today with an expanded polarity vocabulary and contraction mapping before normalization. What is still uncovered: implied negation or domain slang not present in the vocabulary.

## Liquidity Unit Drift

Adapters can accidentally pass dollar or USDC notional where the scanner expects contracts. The scanner mitigates this today by renaming the field to `liquidity_top_contracts` and documenting that adapters must normalize to top-of-book contracts. What is still uncovered: validation against live venue depth units until live read-only adapters exist.

## Quote Staleness

Even if both venues have timestamps, one side can be meaningfully older than the other. The scanner mitigates this today with `ScannerConfig.max_quote_age_seconds` and a pair-relative stale quote cap. What is still uncovered: exchange-specific update cadence and whether a timestamp reflects book capture or API response time.

Live-mode TODO: pair-relative freshness can still pass if both quotes are stale by the same amount; future live adapters must compare the newest quote timestamp to wall-clock `now()`.
