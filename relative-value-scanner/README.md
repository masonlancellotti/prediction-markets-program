# Relative Value Scanner

Read-only scaffold for comparing prediction-market prices against other prediction-market venues and sportsbook reference odds.

This is not a trading bot. It does not connect accounts, place orders, or prepare executable trades.

## Quick Start

```powershell
python -m pytest -q
python scan.py
```

`.env.example` is a template only. Keep real `.env` files local and
uncommitted, and never paste API keys, account IDs, wallet keys, private keys,
tokens, or credentials into ChatGPT, Claude, Codex, or git history.

The offline scan reads fixture data from `venues/fixtures/` and writes:

- `reports/relative_value_candidates.json`
- `reports/relative_value_candidates.md`

The default `python scan.py` run is a static fixture/sample canary, not live market data. Its stdout includes `data_source_mode=STATIC_FIXTURE` and `live_fetch_attempted=false`, and the JSON report includes a `provenance` block with source ids, source types, fixture paths, captured timestamps when present, API-key requirement flags, and live-fetch status.

```powershell
python scan.py source-readiness
python scan.py source-readiness --output reports\source_readiness.json
python scan.py source-smoke
python scan.py discover-live-source-inventory
```

`source-readiness` prints a key-safe API/source checklist for Kalshi, Polymarket, The Odds API, SX Bet, ProphetX, IBKR/ForecastEx, Crypto.com, and Robinhood. It reports whether an API key env var is configured as a boolean only and never prints key values. Kalshi and Polymarket may participate in candidate-pair research, but no single source is reported as able to create a paper candidate by itself.

`source-smoke` is an explicit live-read-only connection smoke test. It loads local `.env` values without printing them, attempts only reviewed public/read-only fetch patterns, reports configured keys as booleans, and keeps planned sources as `LIVE_FETCH_NOT_IMPLEMENTED`.

`discover-live-source-inventory` is explicit-only public inventory discovery for human review. It writes `reports\live_source_inventory.json` and `reports\live_source_inventory.md` with Kalshi series rows, Polymarket tag rows, likely category matches, and profile suggestions. It does not modify overlap profiles, assert overlap or same-payoff, or change any readiness gate.

```powershell
python scan.py fetch-live-readonly --sources kalshi,polymarket,the_odds_api --max-markets 25
python scan.py fetch-live-overlap-universe --category sports --max-markets 500
python scan.py sweep-live-overlap-universe --categories macro,politics,crypto,companies,ai,weather --max-markets 500
python scan.py inspect-live-snapshots
python scan.py match-live-readonly-snapshots
python scan.py enrich-live-match-candidates
python scan.py diagnose-live-matching
```

`fetch-live-readonly` is also explicit-only. It writes sanitized live read-only snapshots and a manifest under `reports\live_readonly\`; it is not used by default `python scan.py`, does not authenticate accounts, does not read balances or positions, does not sign, and does not place or cancel orders. The Odds API output remains `REFERENCE_ONLY`.

`fetch-live-overlap-universe` is an explicit Kalshi/Polymarket-only helper for reducing unrelated live sample comparisons. It fetches read-only market discovery, locally retains a requested category or query, writes updated Kalshi/Polymarket snapshots under `reports\live_readonly\`, and writes overlap diagnostics under `reports\live_overlap_universe_*`. It does not use The Odds API as an executable leg, change matching thresholds, assert same-payoff, or emit candidate actions.

For MLB World Series / KXMLB work, prefer universe-specific saved paths such as `reports\live_readonly\mlb\...` over generic `reports\live_readonly\...` paths so pairs, enriched orderbooks, and summaries are not mixed with another universe's latest fetch.

`sweep-live-overlap-universe` is an explicit non-sports diagnostic loop over macro/economics, politics, crypto, companies, AI, and weather queries. For each query it fetches the overlap universe, inspects saved snapshots, runs saved-snapshot matching, runs diagnostics, and writes `reports\live_overlap_sweep.json` plus `reports\live_overlap_sweep.md`. It is for deciding where to investigate next, not for paper/live readiness.

`inspect-live-snapshots` summarizes saved snapshot shape, safety status, reference-only status, and blockers before any future live matching. Its match-shape readiness fields mean required saved-snapshot identifiers/text/deadlines exist; they do not mean paper-simulation readiness. It writes `reports\live_snapshot_inspection.json` and `reports\live_snapshot_inspection.md` without scoring or action promotion.

`match-live-readonly-snapshots` reads the saved Kalshi and Polymarket snapshots from disk, validates them, and runs the conservative saved-snapshot matcher. It writes `reports\live_readonly_match_report.json` and `reports\live_readonly_match_report.md` with research-only `WATCH`/`MANUAL_REVIEW` rows. It never fetches live APIs and never treats The Odds API reference snapshot as an executable leg.

`enrich-live-match-candidates` reads the current `reports\live_readonly_match_report.json`, selects only existing `WATCH`/`MANUAL_REVIEW` Kalshi/Polymarket pairs, and fetches read-only orderbook metadata for those pair legs only. It writes `reports\live_match_candidate_enrichment.json` and `reports\live_match_candidate_enrichment.md` with depth, orderbook fetch timestamps, quote ages, bid/ask source tags, and fee-model status diagnostics. Kalshi uses the existing reviewed conservative fee model. Polymarket uses the official public CLOB fee formula `C * feeRate * p * (1 - p)` with reviewed category rates, including documented sports taker fee rate `0.03`, and a non-zero conservative unknown-category fallback; it does not assume maker execution or zero fees. The public token/market fee-rate endpoint is not wired into this command yet, so `polymarket_fee_source_used` is `official_category_schedule`, `conservative_unknown`, or `missing_or_unreviewed`. Relationship blockers, weak semantic-only matches, and sports scope/team guardrails remain blockers; fee-adjusted gaps, when present, are diagnostics only and do not grant paper or live readiness. When `contract_relationship.same_payoff` is false, rows include `gross_gap_caveat="same_payoff=false; gross_gap_cents is not arb edge"`.

`sweep-live-overlap-universe` writes per-row labelled snapshots under `reports\live_readonly\sweep\...` so a sweep does not silently leave the default saved live snapshots as the final category/query. Rerun a targeted `fetch-live-overlap-universe` before `enrich-live-match-candidates` when you want the default saved snapshots to represent a specific universe.

`diagnose-live-matching` reads the same saved snapshots and explains rejected Kalshi/Polymarket comparisons without forcing matches, fetching APIs, changing thresholds, or promoting readiness. It writes `reports\live_matching_diagnostics.json` and `reports\live_matching_diagnostics.md`; The Odds API remains reference context only.

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

## Live Read-Only Sportsbook Reference Discovery

```powershell
python scan.py fetch-the-odds-api --sport-key basketball_nba --markets h2h,spreads,totals --output reports\the_odds_api_reference_snapshot.json
python scan.py explain-reference-context --snapshot reports\polymarket_markets_snapshot.json --reference-snapshot reports\the_odds_api_reference_snapshot.json
```

This writes a `schema_version=1`, `schema_kind=reference_snapshot_v1` reference-only snapshot from The Odds API. The API key is read from `THE_ODDS_API_KEY` by default or from `--api-key`; tests use mocked HTTP and do not require a real key. Rows include event title, bookmaker, market type, American odds, implied probability, no-vig probability when calculable, retrieval/stale timestamps, and provenance metadata.

The Odds API and sportsbook rows are `REFERENCE_ONLY`, `is_executable=false`, and `usable_for_trade_decision=false`. Reference snapshots are sibling diagnostic inputs, not executable venue snapshots for the live matcher. No-vig odds are diagnostics, not guaranteed edge. They can inform `WATCH`/diagnostics only and cannot create `PAPER_CANDIDATE`, `PAPER`, or `POSSIBLE_ARB`.

`explain-reference-context` compares one executable snapshot against one reference snapshot for observability only. It reports plausible title/entity matches, bookmaker, market type, no-vig probability, retrieval/stale timestamps, and stale/malformed diagnostics. It does not compute gaps, fees, depth, settlement equivalence, or actions beyond `REFERENCE_ONLY_DIAGNOSTIC`.

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

## LLM Relationship Review Stub

`relative_value/llm_relationship_classifier.py` defines a no-network, no-API-key interface for future LLM relationship review. It is audit metadata only: LLM output cannot assert `EQUIVALENT`, cannot set `same_payoff=true`, cannot approve trades, and cannot change `PAPER_CANDIDATE`, `PAPER`, or `POSSIBLE_ARB` behavior. Deterministic relationship rules remain authoritative.

```powershell
python scan.py llm-review-relationships --input reports\live_snapshot_pairs.json --output reports\live_snapshot_pairs_llm_reviewed.json --stub
```

`llm-review-relationships` is a saved-report audit transformer for matcher/evaluator JSON files that already contain `contract_relationship`. It uses the deterministic stub only, writes `llm_review` sidecars, preserves deterministic relationship fields and actions unchanged, and does not call a real LLM. It never mutates `contract_relationship.manual_review_required`; audit escalation appears only at `llm_review.combined_manual_review_required`, so consumers must inspect both the deterministic relationship and sidecar metadata.

## Live Snapshot Matching Prototype

```powershell
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --reference-snapshot reports\the_odds_api_reference_snapshot.json --output reports\live_snapshot_pairs.json
```

This reads saved schema-v1 snapshot files only. It emits tentative `WATCH` or `MANUAL_REVIEW` pairs for human review and never emits `PAPER`, `PAPER_CANDIDATE`, or `POSSIBLE_ARB`. It does not call live APIs, does not score through `RelativeValueScanner`, and does not claim arb, profit, or executable liquidity. Optional tuning flags are `--min-similarity` and `--max-snapshot-age-hours`.

Optional `--reference-snapshot` files must be `schema_kind=reference_snapshot_v1` and `source_type=REFERENCE_ONLY`. They are loaded into `reference_context` observability diagnostics only, never treated as `normalized_markets`, never candidate legs, and never action-promotion evidence.

The matcher uses strict question/event text overlap, then may add small saved-file-only aids for close settlement times and shared event/league keywords such as NBA, election, BTC, CPI, or Fed. These aids help surface review candidates; they are not settlement-rule proof and cannot produce trading actions.

Each matched pair now includes a deterministic `contract_relationship` block. It classifies relationship evidence such as sports scope mismatch, team-alias mismatch, ambiguous wording, and settlement-window mismatch as review metadata only. Absence of a known mismatch is reported conservatively as `NEAR_EQUIVALENT` with `same_payoff=false`; semantic similarity is not settlement equivalence, and a future LLM reviewer may assist classification but cannot approve candidates by itself.

## Market Graph Consistency Diagnostics

```powershell
python scan.py market-graph-diagnostics
```

This uses static fixtures to write `reports\market_graph_consistency_diagnostics.json` and `reports\market_graph_consistency_diagnostics.md`. It emits only `WATCH` and `MANUAL_REVIEW` relationship diagnostics for exact same payoff, complements, subset/superset, mutually exclusive outcomes, exhaustive groups, overlap-not-equivalent, correlated-only, unrelated, and manual-review cases. It keeps `data_source_mode=STATIC_FIXTURE`, does not call live APIs, and does not integrate into evaluator promotion paths.

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

Use `--accept-unit-mismatch` only to allow otherwise clean positive bid/ask gaps to reach `PAPER_CANDIDATE`; without it, the unit mismatch caps action at `MANUAL_REVIEW`. The CLI default freshness window is 1800 seconds for saved-file workflows. Polymarket uses the conservative CLOB fee estimate by default, while Kalshi uses the conservative tiered estimate. Markout fields are null placeholders for a future saved-snapshot replay pass.

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

## Saved-File Structural Basket Dry Run

```powershell
python scan.py import-kalshi-event-metadata --source path\to\kxevt_2026_demo.json --destination-dir reports\kalshi_event_metadata
python scan.py run-structural-basket-dry-run --snapshot reports\kalshi_orderbook_enriched_snapshot.json --metadata reports\kalshi_event_metadata\kxevt_2026_demo.json --summary-json-output reports\structural_basket_dry_run_summary.json --summary-markdown-output reports\structural_basket_dry_run_summary.md --enriched-snapshot-output reports\structural_basket_dry_run_enriched_snapshot.json --paper-fill-json-output reports\structural_basket_dry_run_paper_fill_journal.json --paper-fill-markdown-output reports\structural_basket_dry_run_paper_fill_journal.md
```

`run-structural-basket-dry-run` is the saved-file-only structural-basket pipeline. It runs every step on disk: audit Kalshi event metadata, join it into the saved snapshot, write the enriched snapshot, build the structural basket review, and then — only if at least one `STOP_FOR_REVIEW` row is surfaced — invoke `simulate-paper-fills` on those rows. If no `STOP_FOR_REVIEW` row exists (because metadata was missing/reference-only/title-only, quotes were stale, depth was insufficient, fees killed the gap, or the join could not match snapshot tickers), the paper-fill simulator is **not** invoked and the summary records `paper_simulation_skipped=true` with a structured `paper_simulation_skip_reason`. Pass `--skip-paper-fill-simulation` to suppress the simulator step even when the detector does surface review-only candidates.

`STOP_FOR_REVIEW` is review/report-only. It never authorizes execution, never promotes a row to `PAPER_CANDIDATE`, and never weakens fee, depth, freshness, settlement, or exhaustiveness gates. The summary's `safety` block reasserts `saved_file_only=true`, `live_fetch_attempted=false`, `places_orders=false`, `auth_used=false`, `private_endpoints_used=false`, `secrets_read=false`, `paper_candidate_emitted=false`, and `stop_for_review_means_review_only=true` on every run.

`import-kalshi-event-metadata` is the saved-file metadata acquisition spec. It reads one or more saved Kalshi event-metadata JSON files from disk, validates them through the existing audit normalizer, optionally copies them into a destination directory, and writes an importer report. It performs zero network I/O, never authenticates, never reads secrets, never calls private endpoints, and never invents live API details. The intended workflow is to save a JSON event-metadata payload to disk by hand (or via a separately-reviewed read-only acquisition tool), then run `import-kalshi-event-metadata` to validate and stage it before `run-structural-basket-dry-run` consumes it.

For the first real dry run, the exact command sequence is:

```powershell
python scan.py import-kalshi-event-metadata --source path\to\your_event_metadata.json --destination-dir reports\kalshi_event_metadata
python scan.py run-structural-basket-dry-run --snapshot reports\kalshi_orderbook_enriched_snapshot.json --metadata reports\kalshi_event_metadata\your_event_metadata.json
```

If the summary reports `stop_for_review_count: 0`, the dry run correctly stopped before paper simulation. Inspect `top_blockers` in the summary to see which gates failed and which artifact to fix next (commonly: real metadata files for the events present in the snapshot, fresher orderbook captures, or markets with deeper top-of-book).

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
