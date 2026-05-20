# Matching Rules

## Canonical Price

All normalized prices use YES probability in `[0, 1]`.

## Match Confidence

`match_confidence` combines event-name similarity, outcome-name similarity, and settlement constraints. The displayed confidence remains weighted toward event similarity, but any candidate needs both event and outcome similarity to be high before it can reach the `POSSIBLE_ARB` threshold.

Opposite comparator polarity caps similarity at `0.30`. Positive polarity currently includes: `over`, `above`, `at_least`, `yes`, `will`, `goes`, `reaches`, `wins`, `win`, `beats`, `beat`, `defeats`, `advances`, `holds`, `passes`, `exceeds`, `hits`. Negative polarity currently includes: `under`, `below`, `at_most`, `no`, `not`, `wont`, `do_not`, `does_not`, `is_not`, `are_not`, `was_not`, `were_not`, `has_not`, `had_not`, `loses`, `lose`, `defeated`, `eliminated`, `drops`, `fails`, `misses`, `falls`.

Different numeric thresholds cap similarity at `0.30`. If either event or outcome similarity is below `0.85`, confidence is capped at `ScannerConfig.min_possible_arb_confidence - ScannerConfig.confidence_cap_headroom_below_arb`, which defaults to `0.07` headroom below the `POSSIBLE_ARB` confidence threshold.

## Settlement Mismatch Risk

- Aligned settlement times: low risk.
- One or both missing settlement times: risk at least `0.25`, confidence capped at `0.75`.
- Settlement times differing by more than 24 hours: risk at least `0.60`, confidence capped at `0.55`.
- Different settlement rules: risk at least `0.25`, confidence capped at `0.80`.
- Empty or incompatible settlement-rule key tokens add `side_definition_unverified`, risk at least `0.25`, and confidence capped at `0.80`.
- Settlement-rule compatibility requires both sides to have at least two key tokens and at least two shared key tokens. Stub rules like `official` fail closed.

## Quote Freshness

- If both `captured_at` values are missing, add `quote_freshness_unverified` and cap below `PAPER`.
- If one side is older than `ScannerConfig.max_quote_age_seconds` relative to the freshest side in the pair, add `stale_quote` and cap at `MANUAL_REVIEW`.
- `captured_at` must be timezone-aware when present.

## Action Ladder

1. `IGNORE`
2. `WATCH`
3. `MANUAL_REVIEW`
4. `PAPER`
5. `POSSIBLE_ARB`

## POSSIBLE_ARB Hard Gates

`POSSIBLE_ARB` requires all of:

- Both sides are executable exchange markets.
- No sportsbook/reference leg.
- Match confidence at least `0.92`.
- Settlement mismatch risk at most `0.05`.
- Positive fee-adjusted gap of at least `0.02`.
- Limiting `liquidity_top_contracts` at least `25`.
- Fee-adjusted gap includes per-leg fees and the conservative NO-side spread penalty when applicable.
- Quotes are fresh and timezone-aware.

If any hard gate fails, action is capped below `POSSIBLE_ARB`.
