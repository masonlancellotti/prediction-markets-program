# Read-Only Orderbook Enrichment

`python scan.py enrich-orderbooks` reads a saved schema-v1 snapshot JSON file and writes a new saved JSON file with an `orderbook_enrichment` section on each normalized market row.

## Scope

- Read-only public orderbook/depth lookup.
- Saved JSON input and output only.
- No authentication, accounts, balances, positions, wallets, order placement, or order cancellation.
- No `RelativeValueScanner` scoring integration.
- No `POSSIBLE_ARB`, profit, or executable-liquidity claim.

## Enrichment Fields

Each market row receives:

- `orderbook_captured_at`
- `best_bid`
- `best_ask`
- `spread`
- `depth_at_best_bid`
- `depth_at_best_ask`
- `depth_within_1c`
- `depth_within_3c`
- `depth_within_5c`
- `source_endpoint`
- `enrichment_status`
- `enrichment_warnings`

Depth-within fields are split into `bid`, `ask`, and `total` buckets. They are raw read-only book sizes from the venue response, not a fillability guarantee.

## Venue Notes

Kalshi orderbooks are normalized into YES-price space. Kalshi exposes YES and NO bid books; NO bids imply YES asks as `1 - no_bid`. Empty books remain `unenriched` with `orderbook_unavailable`.

Polymarket enrichment uses the public CLOB book endpoint for the exact YES token id when the saved snapshot contains an unambiguous `Yes` outcome and matching `clobTokenIds`. If the token id is missing or ambiguous, the row remains `unenriched` with `missing_token_id`.

## Future Use

This layer can later feed paper candidate evaluation by adding current book depth and freshness context. It must not be used as live trading approval without settlement matching, freshness checks, fees, slippage, and repeated paper-fill evidence.
