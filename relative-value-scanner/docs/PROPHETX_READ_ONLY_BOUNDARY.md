# ProphetX Read-Only Research Boundary

## Scope

This is a boundary and readiness plan only. It adds no live ProphetX transport, no authentication or session flow, no account queries, no balance or position access, no order placement, no order cancellation, no browser automation, no scraping, and no execution logic.

`prophetx` is a planned executable-style venue candidate. It cannot create candidate pairs, paper-candidate rows, paper actions, or possible-arbitrage rows.

## Source Identity

- `source_id`: `prophetx`
- Display name: `ProphetX`
- Source type: `EXECUTABLE_VENUE`
- Implementation status: `PLANNED_NOT_IMPLEMENTED`
- Current project mode: fixture-backed schema exists; no live transport
- Execution allowed now: `false`
- Candidate-pair eligible now: `false`
- Paper-candidate eligible now: `false`

## Account and API Requirements

Before any live read-only code can be considered, the user must manually confirm:

- ProphetX account eligibility
- API access approval
- read-only endpoint scope
- market-data permission and any data licensing restrictions
- whether market discovery, orderbook/depth, settlement metadata, and fee data can be read without account, balance, position, or order surfaces

Expected environment variable names for a future reviewed transport are:

- `PROPHETX_BASE_URL`
- `PROPHETX_API_KEY`

These names are readiness placeholders only. They are reported as configured/not configured booleans; values must never be printed or persisted.

## Allowed Read-Only Research Categories

Only these categories are in scope for future reviewed read-only work:

- market discovery: event ids, market ids, titles, market type, status, start/close times, and venue restrictions
- orderbook/depth: true bid/ask, bid/ask depth, quote timestamp, and venue-specific depth units
- settlement metadata: settlement source, rule text, event window, void/cancel rules, and outcome terms
- fee and commission metadata: reviewed maker/taker fees, other venue fees, and fee schedule version
- quote freshness metadata: venue timestamps, delayed/realtime status, and stale quote policy inputs

These categories are diagnostics and normalization inputs only. They are not trade permission.

## Forbidden Categories

The boundary explicitly forbids:

- auth/session routing or credential persistence
- account balances
- positions
- order ids, order routes, order tickets, order placement, cancellation, modification, or fills
- execution previews
- browser automation or scraping
- private keys, signing, or wallet logic
- any code path that can submit, cancel, or route an order

## Market and Settlement Requirements

ProphetX cannot be matched safely until a future reviewed fixture schema or read-only adapter can preserve:

- stable market id and event id
- market title/question
- market type and outcome names
- market status and venue restrictions
- close time and settlement time
- settlement source and rule text
- void/cancel rules
- fee/commission schedule
- true bid/ask and depth
- depth units and quote timestamps

Missing market mapping, unreviewed venue restrictions, missing fee model, stale quotes, missing depth, unknown depth units, or unknown settlement wording must fail closed and block candidate use.

## Redaction Policy

No raw ProphetX network payloads should be persisted until a redaction/filtering pass is reviewed. Future redaction must remove or avoid:

- API keys
- auth headers
- tokens
- sessions
- account ids
- user ids
- order ids
- positions
- balances
- passwords

## Fail-Closed Rules

- no live transport until separate review
- no auth/session/account/balance/position/order queries
- no candidate-pair eligibility without implemented read-only discovery, true bid/ask/depth, quote freshness, reviewed fees, reviewed settlement wording, and strict same-payoff relationship classification
- no paper-candidate rows without executable same-payoff fresh fee-adjusted depth-backed settlement-compatible legs
- no default `scan.py` live API use

## Staged Plan

| Stage | Status | Description |
| --- | --- | --- |
| 0 | Current | Boundary docs, inert metadata, and fixture-backed schema only. |
| 1 | Not implemented | Manual account, API permission, and endpoint-scope review. |
| 2 | Current | Static fixture-backed market, quote/depth, fee, settlement, and restriction schema. |
| 3 | Not implemented | Live read-only transport after separate review; no auth/session/account/order surfaces. |
| 4 | Not implemented | Normalized snapshot generation for manual review only. |
| 5 | Not implemented | Matcher integration only after separate review and all fake-edge gates. |

There is no execution stage in this project scope.
