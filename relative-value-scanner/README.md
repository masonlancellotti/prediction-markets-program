# Relative Value Scanner

Read-only scaffold for comparing prediction-market prices against other prediction-market venues and sportsbook reference odds.

This is not a trading bot. It does not connect accounts, place orders, or prepare executable trades.

## Quick Start

```powershell
python -m pytest -q
python scan.py
```

The offline scan reads fixture data from `venues/fixtures/` and writes:

- `reports/relative_value_candidates.json`
- `reports/relative_value_candidates.md`

## Live Read-Only Polymarket Discovery

```powershell
python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json
python scan.py fetch-polymarket --tag-slug nba --limit 50 --output reports\polymarket_markets_snapshot.json
python scan.py fetch-polymarket --tag-id 100381 --limit 50 --output reports\polymarket_markets_snapshot.json
```

This uses Polymarket's public Gamma discovery API and filters normalized markets to live/useful rows by default: `active=true`, `closed=false`, `archived=false`, `acceptingOrders=true`, and no clearly past end date. Use `--include-closed`, `--include-not-accepting-orders`, or `--include-past-end-date` only for debugging raw venue behavior.

Optional `--tag-slug` and `--tag-id` filters are passed through to the public Gamma events endpoint so snapshots can target a category, sport, or topic. If omitted, the broad active/not-closed discovery request is unchanged.

The snapshot keeps Gamma `best_bid`/`best_ask` discovery numbers when present and per-outcome `outcome_yes_token_price` values. These are discovery fields, not orderbook depth proof. Skip counters can overlap, so their sum is not `market_count - normalized_count`.

The live command does not authenticate, connect a wallet, call trading endpoints, compare against Kalshi, or emit `POSSIBLE_ARB`.

## Live Read-Only Kalshi Discovery

```powershell
python scan.py fetch-kalshi --limit 25 --output reports\kalshi_markets_snapshot.json
python scan.py fetch-kalshi --series-ticker KXNBA --limit 50 --max-pages 2 --output reports\kalshi_markets_snapshot.json
python scan.py fetch-kalshi --event-ticker KXNBA-26MAY20 --limit 50 --output reports\kalshi_markets_snapshot.json
```

This uses Kalshi's public read-only `GET /markets?status=open` endpoint and writes a `schema_version=1` snapshot. Normalized rows filter to useful live/open markets by default: Kalshi `status=open` or `status=active`, not closed/settled/expired, and no clearly past close time.

Optional `--series-ticker` and `--event-ticker` filters are passed through to the public markets endpoint. `--cursor` can start from a returned pagination cursor, and `--max-pages` can follow returned `cursor` or `next_cursor` values across multiple pages. If omitted, the broad one-page `status=open&limit=...` discovery request is unchanged.

The snapshot keeps Kalshi market discovery fields such as YES `best_bid`/`best_ask`, per-outcome `outcome_yes_token_price`, volume, liquidity dollars, close time, and raw payload. These fields are not orderbook-depth proof and are not fed into scanner scoring yet. The command does not authenticate, call order/account endpoints, or emit `POSSIBLE_ARB`.

## Snapshot Schema V1

Both live discovery commands write `schema_version: 1` snapshots. Common top-level fields are `schema_version`, `source`, `captured_at`, `event_count` when available, `market_count`, `normalized_count`, and `normalized_markets`.

Common normalized market fields, when available, are `venue`, `event_id`, `event_title`, `market_id` or `ticker`, `question` or `title`, `outcomes`, `best_bid`, `best_ask`, `volume`, `liquidity`, `end_date` or `close_time`, `active`, `closed`, `status`, and `raw`.

Venue-specific fields are allowed, but consumers should rely only on the documented common fields unless explicitly handling a venue-specific field.

## Source Taxonomy

`relative_value/source_registry.py` defines source types before any broader API expansion:

- `EXECUTABLE_VENUE`
- `REFERENCE_ONLY`
- `SIGNAL_ONLY`

Kalshi and Polymarket are implemented read-only executable venues for candidate-pair research. ForecastEx/IBKR is listed as a planned executable venue but cannot create candidates yet. Manifold and Metaculus are signal-only, while The Odds API and sportsbooks are reference-only. Reference-only sources may inform `WATCH`/diagnostics only; signal-only sources may inform discovery or semantic clustering only. Neither can create `PAPER_CANDIDATE` by itself.

See `docs/SOURCE_TAXONOMY.md` for the planned registry and output policy.

## Live Snapshot Matching Prototype

```powershell
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
```

This reads saved schema-v1 snapshot files only. It emits tentative `WATCH` or `MANUAL_REVIEW` pairs for human review and never emits `PAPER`, `PAPER_CANDIDATE`, or `POSSIBLE_ARB`. It does not call live APIs, does not score through `RelativeValueScanner`, and does not claim arb, profit, or executable liquidity. Optional tuning flags are `--min-similarity` and `--max-snapshot-age-hours`.

The matcher uses strict question/event text overlap, then may add small saved-file-only aids for close settlement times and shared event/league keywords such as NBA, election, BTC, CPI, or Fed. These aids help surface review candidates; they are not settlement-rule proof and cannot produce trading actions.

Each matched pair now includes a deterministic `contract_relationship` block. It classifies relationship evidence such as sports scope mismatch, team-alias mismatch, ambiguous wording, and settlement-window mismatch as review metadata only. Absence of a known mismatch is reported conservatively as `NEAR_EQUIVALENT` with `same_payoff=false`; semantic similarity is not settlement equivalence, and a future LLM reviewer may assist classification but cannot approve candidates by itself.

## Read-Only Orderbook Enrichment

```powershell
python scan.py enrich-orderbooks --snapshot reports\kalshi_markets_snapshot.json --venue kalshi --output reports\kalshi_orderbook_enriched_snapshot.json
python scan.py enrich-orderbooks --snapshot reports\polymarket_markets_snapshot.json --venue polymarket --output reports\polymarket_orderbook_enriched_snapshot.json
```

This reads a saved schema-v1 snapshot and appends `orderbook_enrichment` to each market row. It uses public read-only Kalshi orderbook and Polymarket CLOB book endpoints, does not authenticate, and does not place or cancel orders. The output records best bid/ask, spread, depth at best, depth within 1c/3c/5c, capture time, endpoint, status, and warnings.

Enriched snapshots are not fed into scoring or matching yet. They are future inputs for paper candidate evaluation only, and they make no profit or executable-liquidity claim.

## Read-Only Paper Candidate Ledger

```powershell
python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json --max-quote-age-seconds 1800
```

This reads saved JSON only and emits a manual paper-candidate ledger with actions limited to `WATCH`, `MANUAL_REVIEW`, and `PAPER_CANDIDATE`. It never emits `PAPER` or `POSSIBLE_ARB`, never uses midpoint prices, never fetches live data, and always records the unresolved Polymarket-shares versus Kalshi-contracts unit warning.

Use `--accept-unit-mismatch` only to allow otherwise clean positive bid/ask gaps to reach `PAPER_CANDIDATE`; without it, the unit mismatch caps action at `MANUAL_REVIEW`. The CLI default freshness window is 1800 seconds for saved-file workflows. Polymarket fees default to no-fee comparison, while Kalshi uses the conservative tiered estimate. Markout fields are null placeholders for a future saved-snapshot replay pass.

Paper ledger rows also carry `contract_relationship` for debugging and future gating. The evaluator re-classifies from relationship-level matcher blocking reasons plus the unresolved Polymarket-shares versus Kalshi-contracts warning when that warning is relevant; it does not copy matcher confidence/source through. Sportsbook or reference odds remain non-executable reference prices.

## Saved-File Markout Replay

```powershell
python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot_later.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot_later.json --output reports\paper_candidates_ledger_marked.json
```

This reads an existing paper candidate ledger plus later saved enriched snapshots and fills only markout windows whose later quote timestamps are within tolerance of `t_plus_30s`, `t_plus_5m`, `t_plus_30m`, or `t_plus_2h`. It matches by Polymarket `market_id` and Kalshi `ticker`, reuses the original ledger's bid/ask direction, and applies the same evaluator fee defaults.

Markout replay is research evidence only. It does not call live APIs, use midpoint fills, assume execution, walk books, claim profit, prove settlement equivalence, or emit `PAPER` / `POSSIBLE_ARB`. Missing, stale, too-early, or too-late windows stay null with `markout_status`.

## Targeted Pipeline Runner

```powershell
python scan.py run-targeted-pipeline --polymarket-tag-slug nba --kalshi-series-ticker KXNBA --label nba_kxnba
python scan.py run-targeted-pipeline --polymarket-tag-slug nba --kalshi-series-ticker KXNBA --label nba_kxnba_looser_review --max-settlement-delta-seconds 43200
```

This runs the read-only saved-file workflow for one target universe: Polymarket discovery, Kalshi discovery, orderbook enrichment, saved snapshot matching, and paper candidate evaluation. Outputs are labeled under `reports/`, for example `reports/nba_kxnba_pairs.json` and `reports/nba_kxnba_paper_candidates.json`.

The runner prints normalized counts, enrichment counts, pair count, evaluator action counts, top rejection reasons, and the exact `replay-paper-candidate-markouts` command to run later after separate later snapshots have been captured. It forwards evaluator review knobs such as `--max-settlement-delta-seconds`, `--min-net-gap`, `--min-top-of-book-size`, and `--accept-unit-mismatch` without changing their defaults. It does not sleep, trade, authenticate, score through `RelativeValueScanner`, use midpoint fills, claim profit, or emit `PAPER` / `POSSIBLE_ARB`.

## Action Ladder

- `IGNORE`
- `WATCH`
- `MANUAL_REVIEW`
- `PAPER`
- `POSSIBLE_ARB`

`POSSIBLE_ARB` is intentionally rare and requires two executable exchange venues, high match confidence, low settlement mismatch risk, fresh quotes, positive fee-adjusted gap, and enough top-of-book contract liquidity.

- Fee model: fee-adjusted gaps use per-leg `FeeModel` estimates.
- Polarity: opposite YES-side language such as win/lose or over/under fails closed.
- NO-side penalty: assumed NO offsets subtract a conservative spread penalty.
