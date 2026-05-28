# Crypto.com Predict / CDNA Read-Only Research Boundary

## Scope

This is a boundary and fixture plan only. It adds no live Crypto.com Predict/CDNA transport, no authentication or session flow, no account queries, no balance or position access, no order placement, no order cancellation, no wallet or signing logic, no browser automation, no scraping, and no execution logic.

`crypto_com_predict_cdna` is a planned executable event-contract venue. It is not a generic crypto exchange and not a reference or truth feed. It cannot create candidate pairs, paper-candidate rows, paper actions, or possible-arbitrage rows.

## Source Identity

- `source_id`: `crypto_com_predict_cdna`
- Display name: `Crypto.com Predict / CDNA`
- Source type: `EXECUTABLE_VENUE`
- Implementation status: `PLANNED_NOT_IMPLEMENTED`
- Current project mode: boundary and fake fixture schema only; no live transport
- Execution allowed now: `false`
- Candidate-pair eligible now: `false`
- Paper-candidate eligible now: `false`

## Account, Region, and API Requirements

Before any live read-only code can be considered, the user must manually confirm:

- Crypto.com Predict/CDNA region and eligibility constraints
- whether public read-only market data exists
- whether market discovery, orderbook/depth, settlement metadata, and fee data can be read without account, balance, position, order, wallet, or signing surfaces
- whether event-contract mechanics map cleanly to the normalized market contract
- how fees, payout terms, settlement sources, void rules, and quote freshness should be reviewed

No environment variables or credential placeholders are defined by this boundary.

## Allowed Read-Only Research Categories

Only these categories are in scope for saved-file fixture research:

- market discovery: market ids, event ids, title, market type, status, start time, and close time
- orderbook depth: bid, ask, bid size, ask size, quote timestamp, and explicit depth units
- settlement metadata: settlement source, rule text, close time, and void/cancellation rules
- fee metadata: reviewed fee schedule version, maker fee, and taker fee
- region eligibility: manual region and eligibility review notes without account or identity data

These categories are diagnostics and normalization inputs only. They are not trade permission.

## Forbidden Categories

The boundary explicitly forbids:

- auth/session routing or credential persistence
- account balances
- positions
- account identifiers
- order ids, order routes, order tickets, order placement, cancellation, modification, or fills
- execution previews
- browser automation or scraping
- private keys, signing, or wallet logic
- any code path that can submit, cancel, route, or preview an order

## Market and Settlement Requirements

Crypto.com Predict/CDNA cannot be matched safely until a future reviewed fixture schema or separately approved read-only adapter can preserve:

- stable market id and event id
- market title/question
- market type and outcome names
- market status and venue restrictions
- start, close, and settlement timing
- settlement source and rule text
- void/cancel rules
- fee schedule
- true bid/ask and depth
- depth units and quote timestamps

Missing market mapping, unreviewed region eligibility, missing fee model, stale quotes, missing depth, unknown depth units, or unknown settlement wording must fail closed and block candidate use.

## Redaction Policy

No raw live payloads should be persisted. Future redaction must remove or avoid:

- account identifiers
- session tokens
- auth headers
- API keys
- usernames or passwords
- cookies
- order ids
- positions
- balances
- wallet addresses where tied to a user account
- private keys or signing material

## Fail-Closed Rules

- no live transport until separate review
- no auth/session/account/balance/position/order queries
- no wallet or signing code
- no candidate-pair eligibility without implemented reviewed read-only discovery, true bid/ask/depth, quote freshness, reviewed fees, reviewed settlement wording, and strict same-payoff relationship classification
- no paper-candidate rows without executable same-payoff fresh fee-adjusted depth-backed settlement-compatible legs
- no default `scan.py` live API use

## Staged Plan

| Stage | Status | Description |
| --- | --- | --- |
| 0 | Current | Boundary docs, inert metadata, and fake fixture schema only. |
| 1 | Not implemented | Manual region, permission, and execution-mechanics review. |
| 2 | Current | Static fixture-backed market, quote/depth, fee, settlement, and region schema. |
| 3 | Not implemented | Live read-only transport after separate review; no auth/session/account/order surfaces. |
| 4 | Not implemented | Normalized saved snapshots for manual review only. |
| 5 | Not implemented | Matcher or evaluator integration only after separate review and all fake-edge gates. |

There is no execution stage in this project scope.
