# IBKR / ForecastEx Read-Only Research Boundary

## Scope

This is a boundary and readiness plan only. It adds no IBKR transport, no TWS/Gateway connection, no Client Portal session, no authentication flow, no account queries, no balance or position access, no order placement, no order cancellation, and no execution logic.

`forecastex_ibkr` remains `PLANNED_NOT_IMPLEMENTED` in the source registry. It cannot create candidate pairs, paper-candidate rows, paper actions, or possible-arbitrage rows.

## Source Identity

- `source_id`: `forecastex_ibkr`
- Display name: `IBKR / ForecastEx`
- Source type: `EXECUTABLE_VENUE`
- Implementation status: `PLANNED_NOT_IMPLEMENTED`
- Current project mode: fixture-backed schema exists; no live transport
- Execution allowed now: `false`
- Candidate-pair eligible now: `false`
- Paper-candidate eligible now: `false`

## Account and Permission Requirements

IBKR / ForecastEx is high-value but high-friction. Before any live read-only transport can be considered, the user must manually confirm:

- an eligible IBKR account exists
- ForecastEx event-contract access is enabled
- relevant market-data permissions are enabled
- read-only API usage is permitted for instrument discovery and market data
- the boundary can avoid account balances, positions, order state, order routes, and execution surfaces

Expected environment variable names for a future reviewed transport are:

- `IBKR_HOST`
- `IBKR_PORT`
- `IBKR_CLIENT_ID`
- `IBKR_ACCOUNT_ID`

These names are readiness placeholders only. They are reported as configured/not configured booleans; key or credential values must never be printed or persisted.

## Allowed Read-Only Research Categories

Only these categories are in scope for future reviewed read-only work:

- instrument discovery: contract id, symbol, exchange, trading class, expiration, currency, and ForecastEx event-contract title
- market data snapshots: bid, ask, bid size, ask size, delayed/realtime status, and quote timestamp
- settlement metadata: settlement source, rule text, event window, contract multiplier, and yes/no payout terms
- fee and commission metadata: reviewed commission schedule, per-contract fees, exchange/regulatory fees if applicable
- quote freshness metadata: venue timestamps and stale/delayed quote status

These categories are diagnostics and normalization inputs only. They are not trade permission.

## Forbidden Categories

The boundary explicitly forbids:

- account balances
- positions
- account identifiers beyond non-secret readiness booleans
- order ids, routes, placement, cancellation, modification, or fills
- order tickets, presets, or execution previews
- auth/session routing or browser automation
- private keys, signing, wallet logic, or credential persistence
- any code path that can submit, cancel, or route an order

## Instrument Discovery Requirements

IBKR / ForecastEx cannot be matched safely until instruments are mapped to stable identifiers and settlement terms:

- venue contract id or `conid`
- event-contract title
- yes/no payoff terms
- expiration and final trading time
- settlement source and rule text
- contract multiplier and currency
- market status and tradability restrictions

Missing instrument mapping must fail closed and block candidate use.

## Quote, Depth, Fee, and Freshness Requirements

Future read-only snapshots must preserve:

- true bid/ask, not midpoint or inferred prices
- depth at bid/ask with units clearly specified
- quote timestamps and delayed/realtime status
- quote age and stale quote policy
- reviewed fee/commission schedule
- settlement rule text and restriction metadata

Missing fee model, stale/delayed quotes, missing depth, unknown settlement wording, or ambiguous contract relationship must force review/rejection. No same-payoff assertion may be inferred from title similarity.

## Redaction Policy

No raw network payloads should be persisted until a redaction/filtering pass is reviewed. Future redaction must remove or avoid:

- account identifiers
- session tokens
- auth headers
- usernames or passwords
- Client Portal cookies
- order ids
- permanent ids
- positions
- balances

## Fail-Closed Rules

- no live transport until separate review
- no account/balance/position/order queries
- no candidate-pair eligibility without implemented read-only discovery, true bid/ask/depth, quote freshness, reviewed fees, reviewed settlement wording, and strict same-payoff relationship classification
- no `PAPER_CANDIDATE` without executable same-payoff fresh fee-adjusted depth-backed settlement-compatible legs
- no default `scan.py` live API use

## Staged Plan

| Stage | Status | Description |
| --- | --- | --- |
| 0 | Current | Boundary docs, inert metadata, and fixture-backed schema only. |
| 1 | Not implemented | Manual account, permission, and API boundary review. |
| 2 | Current | Static fixture-backed instrument, quote, fee, and settlement schema. |
| 3 | Not implemented | Live read-only transport after separate review; no account/order queries. |
| 4 | Not implemented | Normalized snapshot generation for manual review only. |
| 5 | Not implemented | Matcher integration only after separate review and all fake-edge gates. |

There is no execution stage in this project scope.
