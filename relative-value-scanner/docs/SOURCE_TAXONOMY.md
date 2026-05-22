# Source Taxonomy

## Purpose

More APIs increase candidate volume and fake-edge risk. Every source must be classified before its data can affect scanner output, because executable venues, reference-only prices, and signal-only forecasts have different safety boundaries.

This taxonomy is infrastructure only. It does not add API fetchers, authentication, account access, order logic, private keys, or live execution.

## Source Types

- `EXECUTABLE_VENUE`: a prediction-market venue that may eventually support cross-venue candidate pairs after read-only discovery, normalization, relationship classification, settlement checks, liquidity checks, fee checks, and freshness checks. This does not imply live trading.
- `REFERENCE_ONLY`: a source with prices or probabilities useful for `WATCH` rows, diagnostics, calibration, or sanity checks, but not executable in this scanner.
- `SIGNAL_ONLY`: a source useful for discovery, semantic clustering, or forecast context, but not for pricing an executable candidate.
- `DO_NOT_USE_YET`: a known source or protocol that should not affect scanner output until a separate schema, legality, settlement, or execution-permission review is complete.

## Planned Registry

| Source | Type | Status | Allowed impact |
| --- | --- | --- | --- |
| Kalshi | `EXECUTABLE_VENUE` | Implemented read-only discovery/enrichment | May participate in candidate pairs with another implemented executable venue. |
| Polymarket | `EXECUTABLE_VENUE` | Implemented read-only discovery/enrichment | May participate in candidate pairs with another implemented executable venue. |
| ForecastEx / IBKR | `EXECUTABLE_VENUE` | Planned, not implemented | Boundary design exists in `docs/IBKR_FORECASTEX_READ_ONLY_BOUNDARY.md`; no candidate-pair impact because auth/account/instrument work is not implemented. |
| SX Bet | `EXECUTABLE_VENUE` | Planned, not implemented | Candidate venue candidate for read-only research only; no wallet/signing/execution logic. |
| Azuro | `DO_NOT_USE_YET` | Planned, not implemented | On-chain AMM/protocol model needs separate schema work before scanner use. |
| Omen / Gnosis Conditional Tokens | `DO_NOT_USE_YET` | Planned, not implemented | Conditional-token/indexer model needs token, collateral, oracle, and settlement review first. |
| PredictIt | `DO_NOT_USE_YET` | Planned, not implemented | Do not treat as executable unless permitted execution API support is proven. |
| Manifold | `SIGNAL_ONLY` | Planned, not implemented | Discovery and semantic clustering only. |
| Metaculus | `SIGNAL_ONLY` | Planned, not implemented | Discovery and semantic clustering only. |
| The Odds API / sportsbooks | `REFERENCE_ONLY` | Implemented read-only reference snapshots | `WATCH` diagnostics only. |

## Output Policy

- Implemented executable venues may produce candidate pairs, subject to all existing fake-edge gates.
- Reference-only sources may inform `WATCH` rows and diagnostics only.
- Signal-only sources may help discovery or semantic clustering only.
- No reference-only or signal-only source can create `PAPER_CANDIDATE` by itself.
- Planned executable venues cannot create candidate pairs until a separate reviewed implementation exists.

Source type must be checked before candidate evaluation. A source being listed here is not permission to fetch it live, authenticate, trade, or treat its quotes as executable liquidity.

`python scan.py source-readiness` exposes a key-safe operational checklist for implemented, reference-only, and planned sources. It reports whether expected API-key environment variables are configured as booleans only, whether live fetches are implemented, whether default `scan.py` uses the source live, and whether the source can participate in paper-candidate review.

## Review Boundaries

Semantic similarity is not settlement equivalence. Contract relationship classification may later use LLM assistance, API metadata, or manual review, but an LLM cannot approve candidates alone. A future affirmative relationship result would still need source-type, settlement, freshness, fee, and liquidity checks before any paper-candidate review.

Weather remains the only proprietary edge domain for now because it has external observations, forecasts, settlement labels, and replay data. This repo remains infrastructure for matching, normalization, reference comparison, source taxonomy, and fake-edge prevention.
