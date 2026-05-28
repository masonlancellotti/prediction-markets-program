# Operator Arb Convergence Playbook

Operator-approved residual-risk rows are not mathematically strict exact arbitrage. They are scoped review rows where an operator has accepted a specific residual tail risk, such as MLB World Series no-champion/no-contest handling differences.

## Positive Edge Is Not Enough

A positive net edge can still be a poor use of capital if settlement is months away. A 0.6 cent edge on a pair can be real but unattractive when annualized over a long holding period, especially after fees and operational review costs.

## Hold Versus Convergence

Hold-to-settlement economics measure the return if the pair is kept until final resolution and the normal-state payoff is received.

Convergence economics ask a different question: can both legs be exited early at bids that lock in enough profit before capital is tied up for too long?

## Hedged Pair Downside

The pair is structured so normal-state outcomes should pay roughly 1.0 across the two legs. That normal-state hedge is useful downside protection, but it does not remove residual rules risk, venue risk, stale quotes, fee uncertainty, or exit-liquidity risk.

## Early Exit Requires Bid Data

The convergence plan refuses to estimate current exit value unless bid-side data is present for both legs. Entry asks alone are not enough to plan an early exit. Missing exit bids must be treated as a monitoring blocker, not guessed from midpoint, last price, title, or complement math.

## Why Standard Paper Candidates Stay Off

Operator-approved arb rows do not enter the standard paper-candidate pipeline. The global exact-arb gates remain unchanged, `exact_ready` stays false, and standard paper candidates are not emitted from these reports.

## Review Checklist

- Net edge after conservative fees.
- Available notional and size-unit interpretation.
- Annualized hold-to-settlement return.
- Capital tie-up period.
- Target exit pair value and target exit return.
- Current bid-side exit liquidity.
- Quote freshness.
- Remaining residual-risk notes and blockers.
