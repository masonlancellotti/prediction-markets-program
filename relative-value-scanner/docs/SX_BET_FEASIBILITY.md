# SX Bet Read-Only Feasibility Spike

## Scope

This spike is a static, fixture-backed design layer for SX Bet. It does not call SX Bet live APIs, does not require an API key, and does not add wallet, signing, private-key, auth/session, order, cancel, routing, account, balance, position, or execution logic.

SX Bet remains `PLANNED_NOT_IMPLEMENTED` in the source registry. It cannot create candidate pairs, `PAPER_CANDIDATE`, `PAPER`, or `POSSIBLE_ARB`.

## Research Snapshot Shape

The fixture parser in `venues/sx_bet.py` creates a separate research snapshot:

- `schema_version: 1`
- `schema_kind: sx_bet_research_snapshot_v1`
- `source: sx_bet_research`
- `source_id: sx_bet`
- `source_type: EXECUTABLE_VENUE`
- `implementation_status: PLANNED_NOT_IMPLEMENTED`
- `permission: READ_ONLY_RESEARCH`
- `is_executable: false`
- `can_create_candidate_pair: false`
- `can_create_paper_candidate: false`
- `research_markets`, not `normalized_markets`

This shape is intentionally not executable schema-v1. It is for feasibility review only.

The static fixture currently echoes raw fields into research rows for local review/debugging. That is safe only for hand-authored fixtures. Before any future networked adapter exists, raw response persistence must be filtered or redacted so auth tokens, wallet addresses, session data, signatures, maker/taker identifiers, or other sensitive fields cannot be stored accidentally. Wallet, signing, and execution remain explicitly out of scope.

## Candidate Adapter Shape

## Future Live-Read-Only Boundary

No live adapter exists yet. The intended live-read-only boundary is documented as inert metadata in `relative_value/sx_bet_live_read_only_boundary.py`; it imports no transport or blockchain libraries and performs no network calls.

### Intended Endpoint Categories

The future public read-only adapter, if separately approved, should only target endpoint categories like:

- market discovery: markets/events by `marketHash`
- active orders/orderbook: maker odds, maker side, total size, fill amount, expiry
- trade history: trade prints and timestamps for diagnostics

Realtime orderbook subscriptions may help freshness later, but SX Bet realtime docs describe an authenticated token flow, so that remains out of scope for this checkpoint. Posting/filling/canceling orders is an explicit forbidden execution surface.

### Staged Implementation Plan

| Stage | Status | Description |
| --- | --- | --- |
| 0 | Current | Static fixture parser only. |
| 1 | Not implemented | Live-read-only raw fetcher, disabled by default. |
| 2 | Not implemented | Raw snapshot archival with redaction. |
| 3 | Not implemented | Schema validation and quote freshness checks. |
| 4 | Not implemented | Normalized snapshot generation for manual review only. |
| 5 | Not implemented | Matcher integration only after separate review. |

There is no execution stage in this project scope.

### Raw Archival and Redaction Policy

Static fixture raw echo is intentional for local review/debugging. Future networked raw snapshots must not persist unfiltered venue payloads. Redaction/filtering must remove or avoid auth headers, realtime tokens, wallet addresses, maker/taker identifiers, session data, signatures, private keys, executor fields, salts, nonces, affiliate addresses, EIP-712 signatures, relayer fields, and any sensitive execution-adjacent fields before archival.

### Rate Limit and Retry Assumptions

A future live-read-only fetcher should assume public endpoints have rate limits. It should use bounded timeouts, a small retry limit, bounded exponential backoff with jitter, and fail closed on HTTP errors or malformed payloads. This spike adds none of that transport behavior.

### Fail-Closed Rules

- SX Bet live-read-only snapshots remain non-executable by default.
- `can_create_candidate_pair=false` until a separate approved implementation changes registry and capability gates.
- Missing fee model, stale quotes, unknown settlement wording, missing depth, or ambiguous event/line/period equivalence must force `WATCH`, `MANUAL_REVIEW`, or rejection.
- No `PAPER_CANDIDATE` without executable legs, strict same-payoff relationship classification, fresh quotes, fee adjustment, depth support, and settlement compatibility.

### Market Discovery Fields

Potential SX Bet market discovery should preserve:

- `marketHash`
- event/fixture title
- league and sport labels
- market `type`
- spread/total `line`
- `mainLine`
- market status
- scheduled start time
- `outcomeOneName`
- `outcomeTwoName`
- `outcomeVoidName`
- raw venue payload

The docs describe `marketHash` as the primary key used to fetch orders, post/cancel orders, query trade history, and subscribe to orderbook updates. Only read-only uses are in scope here.

### Orderbook and Depth Fields

SX Bet orders are maker-perspective. The research parser keeps:

- `isMakerBettingOutcomeOne`
- `percentageOdds`
- `totalBetSize`
- `fillAmount`
- derived taker price
- available maker stake in USDC
- best taker price per outcome
- USDC depth at best taker price

Important unit warning: `totalBetSize - fillAmount` is maker stake in USDC, not normalized prediction-market contracts. It cannot be treated as the same unit as Kalshi contracts or Polymarket shares without additional review.

### Trade and Settlement Metadata

Future read-only research should preserve:

- settlement source text
- settlement rule text
- void/cancel rule text
- market status and suspension/settled states
- trade print ids and timestamps if fetched read-only later

Settlement wording must be normalized and compared deterministically before any candidate-pair eligibility.

### Fee Metadata

The research snapshot labels SX Bet fees as `not_normalized`. Public docs describe no fees for single bets and separate parlay fee behavior, but this project does not yet have a reviewed SX Bet fee model. No SX Bet row can pass evaluator fee gates until a conservative fee model exists.

### Quote Freshness Fields

The fixture snapshot stores `captured_at` and per-market `quote_captured_at`. A real read-only adapter would also need venue timestamps from market/order updates, a stale quote policy, and recovery handling for REST plus websocket gaps. No websocket/auth flow is implemented.

### Restrictions and Risks

Unresolved blockers:

- wallet/private-key/signing would be required for execution, which is out of scope
- no reviewed SX Bet fee model
- USDC stake depth is not normalized to contracts/shares
- sports period, line, void, overtime, and settlement-source equivalence need guardrails
- market status/suspension behavior needs fail-closed handling
- live websocket data requires auth token flow, which is out of scope
- no integration with matcher, evaluator, scanner, or pipeline

## Requirements Before Candidate Eligibility

SX Bet could only be reconsidered for candidate-pair eligibility after all of these are true:

- implemented read-only market adapter
- implemented read-only orderbook/depth adapter
- real bid/ask/depth confirmed and unit-normalized
- quote freshness policy
- conservative fee model
- settlement wording normalization
- strict same-payoff relationship classification
- venue restrictions reviewed
- no wallet/private-key/signing/execution logic in this project stage

This spike does not lower readiness gates.

## Sources Checked

- SX Bet developer hub: public REST data access is available for reading markets, odds, and orderbooks, while wallet-based auth/signing is separate.
- SX Bet markets docs: markets use `marketHash`, outcome names, market type, line, and main-line fields.
- SX Bet orderbook docs: orders are maker-perspective with `isMakerBettingOutcomeOne`, `percentageOdds`, `totalBetSize`, and `fillAmount`; taker prices require conversion.
- SX Bet real-time docs: websocket orderbook/trade updates use authenticated realtime token flow and are out of scope for this spike.
