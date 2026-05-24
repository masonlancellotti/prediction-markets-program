# Next Executable Venue Expansion Plan

## Purpose

The next expansion should add executable-venue coverage only after fake-edge guardrails are explicit. This is a design plan, not an adapter implementation. It adds no live fetcher, authentication, account access, wallet, private-key, signing, order placement, or execution behavior.

Reference odds, sportsbook odds, LLM output, and semantic similarity are not settlement equivalence. `PAPER_CANDIDATE` remains gated by executable legs, same-payoff classification, real bid/ask/depth, fees, slippage/top-of-book size, quote freshness, and settlement wording.

## Recommended Next Adapter

Recommended next build: **SX Bet read-only market/orderbook research adapter**.

Why:

- Public docs describe live market, odds, orderbook, and trade/trend data access without requiring an API key for read-only fetching.
- It can exercise a new executable-venue adapter shape without IBKR-style account/permission complexity.
- It must remain read-only: no wallet connection, signing, maker/taker order flow, or FillOrder/settlement-contract calls.
- It must not create `PAPER_CANDIDATE` until settlement metadata, fee/slippage treatment, quote freshness, source restrictions, and contract relationship equivalence are reviewed.

ForecastEx / IBKR is likely the highest-value regulated venue, but should be deferred because market data and instrument access likely cross into authentication, account permissions, eligibility, and broker API complexity. The current boundary is documented in `docs/IBKR_FORECASTEX_READ_ONLY_BOUNDARY.md`; it is design-only and adds no live transport. ProphetX now has a separate boundary and fixture schema in `docs/PROPHETX_READ_ONLY_BOUNDARY.md`; live API access, endpoint permissions, fees, and settlement metadata remain unreviewed. PredictIt should not be treated as executable unless permitted execution API support is proven. Azuro and Omen/Gnosis should be deferred because on-chain AMM/protocol/conditional-token models do not fit the current schema-v1 bid/ask/depth assumptions cleanly.

## Adapter Interface Design

Future executable-venue adapters should be split into independent read-only components:

| Component | Responsibility | Current allowed behavior |
| --- | --- | --- |
| `MarketDiscoveryClient` | List markets/events/contracts and raw venue ids. | Read-only public or mocked calls only. |
| `OrderbookDepthClient` | Fetch top-of-book bid/ask, depth, quote timestamps, and venue-specific units. | Read-only public or mocked calls only. |
| `TradeSettlementMetadataClient` | Fetch settlement text, event rules, oracle/source, expiration, and restriction metadata. | Read-only public or mocked calls only. |
| `FeeScheduleProvider` | Produce conservative fee estimates and unit warnings. | Static or read-only documented fee inputs only. |
| `QuoteFreshnessPolicy` | Convert venue timestamps into stale/fresh diagnostics. | Saved-file evaluation only. |
| `VenueRestrictionPolicy` | Encode eligibility, jurisdiction, market status, transferability, and withdrawal restrictions. | Diagnostic gating only. |
| `FutureExecutionClient` | Place/cancel orders or manage positions. | Must remain absent/unimplemented. |

The normalized output should preserve schema-v1 concepts only when they are real:

- `normalized_markets` for executable venue market discovery.
- `best_bid` / `best_ask` only from true venue bid/ask fields.
- `depth_at_best_bid` / `depth_at_best_ask` only from true depth/orderbook fields.
- `orderbook_captured_at` and venue timestamps for freshness.
- `settlement_rule`, `end_date`, `close_time`, and raw settlement text.
- `venue_restrictions` for geography, eligibility, account, wallet, transfer, or market-state issues.

No adapter may create execution methods until a separate review explicitly allows that work.

## Capability Matrix

| Source | Classification | Public market data | Bid/ask | Depth | Trades | Settlement rules | Auth for data | Wallet/private key | Execution API exists | Execution allowed now | Can create paper candidate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ForecastEx / IBKR | `EXECUTABLE_VENUE` | No | Yes | Yes | Yes | Yes | Yes | No | Yes | No | No |
| SX Bet | `EXECUTABLE_VENUE` | Yes | Yes | Yes | Yes | Yes | No | Yes | Yes | No | No |
| Azuro | `DO_NOT_USE_YET` | Yes | No | No | Yes | Yes | No | Yes | Yes | No | No |
| Omen / Gnosis Conditional Tokens | `DO_NOT_USE_YET` | Yes | No | No | Yes | Yes | No | Yes | Yes | No | No |
| PredictIt | `DO_NOT_USE_YET` | Yes | Yes | No | Yes | Yes | No | No | Not proven | No | No |

## Venue Notes

### ForecastEx / IBKR

Classify as `EXECUTABLE_VENUE`, planned. It is high-value because ForecastEx contracts are regulated event contracts and IBKR has brokerage infrastructure, but it is high-friction because data, permissions, eligibility, and instrument mapping likely require authenticated broker workflows. Do not build live transport until account/API permissions, instrument discovery, settlement metadata, fee/commission modeling, quote freshness, and raw redaction are explicitly reviewed.

### SX Bet

Classify as `EXECUTABLE_VENUE`, planned. Build first only as read-only research for public markets/orderbooks. SX Bet docs describe programmatic order posting and on-chain fill mechanics, so any execution work would imply wallet/signing/order logic and is out of scope. Sports markets also require strict competition, line, period, and settlement-source equivalence guardrails.

### Azuro

Classify as `DO_NOT_USE_YET`. Azuro is a protocol/liquidity layer with AMM-style pricing rather than a simple orderbook. It needs a separate schema for pool/liquidity, oracle, collateral, and payout mechanics before it can be compared safely.

### Omen / Gnosis Conditional Tokens

Classify as `DO_NOT_USE_YET`. Conditional-token markets require token id, collateral, condition id, oracle, payout vector, and redemption analysis. Treating token or AMM prices as simple schema-v1 bid/ask would create fake-edge risk.

### PredictIt

Classify as `DO_NOT_USE_YET`. Public market data can be useful for later diagnostics, but it must not be executable or paper-candidate eligible unless permitted execution API support and all venue restrictions are proven.

## Paper-Candidate Gate

Only pairs satisfying all of the following can ever be paper-candidate eligible:

- both legs are implemented executable venues
- deterministic relationship is affirmative same-payoff equivalence
- no relationship blocking reasons
- true bid/ask and depth are present
- fee model is available
- top-of-book size and slippage are handled conservatively
- quotes are fresh and timestamped
- settlement wording/rules are available and compatible
- venue restrictions do not block participation

This plan does not lower thresholds or readiness gates.

## Sources Checked

- SX Bet docs describe public live odds/orderbook APIs and separate programmatic order posting / on-chain fill mechanics.
- Azuro docs describe a protocol using dynamic AMM/liquidity-pool mechanics rather than a standard orderbook.
- Gnosis Conditional Tokens docs describe tokenized outcomes, conditions, collateral, and redemption mechanics.
- PredictIt public pages show live markets and orderbook UI, while public API references are read-only; permitted execution API support is not treated as proven.
- IBKR/ForecastEx public materials indicate ForecastEx event contracts are available through broker/trading-permission workflows, which makes it high-value but high-friction.
