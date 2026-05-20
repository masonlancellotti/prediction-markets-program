# Current Status

## Prior Milestones

- 2026-05-19 Initial Scaffold: created the read-only fixture scanner, core models, adapters, reports, docs, and first tests.
- 2026-05-19 False-Arb Hardening: added polarity/threshold caps, fee models, NO-side penalty, fail-closed executable flags, and false-arb docs.

## 2026-05-19 Freshness and Liquidity Hardening

Completed:

- Added timezone-aware `captured_at` to `NormalizedMarket`.
- Added quote freshness checks with `stale_quote` and `quote_freshness_unverified` caps.
- Renamed market liquidity to `liquidity_top_contracts` and locked the unit to top-of-book contracts.
- Added deterministic fixture `captured_at` values for exchange fixtures.
- Expanded polarity vocabulary for win/lose, beat/defeat, pass/fail, hit/miss, and related verbs.
- Added contraction mapping before text normalization.
- Tightened settlement-rule compatibility to reject stub rules.
- Guarded sportsbook-vs-sportsbook reference comparisons.
- Derived the low-component confidence cap from `ScannerConfig.min_possible_arb_confidence`.
- Prioritized high-risk report notes.
- Removed the `--offline` flag from `scan.py`; fixture mode is the only mode.
- Exported scanner/config/scoring/fee classes from `relative_value/__init__.py`.
- Refreshed `docs/sample_report/relative_value_candidates.md`.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `Get-Content reports\relative_value_candidates.md`

Tests run:

- `python -m pytest -q`: superseded by v1.1 result below.

What works:

- Stale or freshness-unverified quotes cannot reach `PAPER`/`POSSIBLE_ARB`.
- Liquidity gates use explicit top-of-book contract units.
- Opposite-polarity language and numeric-threshold drift continue to fail closed.
- Fixture scan produced 11 candidates and 0 `POSSIBLE_ARB`.

What remains stubbed:

- All adapters are fixture-only.
- No live API quote capture or real freshness proof.
- Fee models remain conservative placeholders.
- No database, scheduler, web dashboard, accounts, or trading endpoints.

## 2026-05-19 v1.1 Polish

Completed:

- Added `quote_freshness_unverified` and `both_sides_sportsbook_reference` to report reason priority.
- Added a test that representative scoring reasons are covered by `_REASON_PRIORITY`.
- Moved confidence-cap headroom to `ScannerConfig.confidence_cap_headroom_below_arb`.
- Suppressed redundant opposite-side sportsbook reference candidates by default.
- Left `include_ignore=True` as the exhaustive/debug scan path.
- Added the live-mode absolute freshness TODO in `_quote_freshness_cap`.
- Removed dead `wont` vocabulary while preserving `won't` contraction handling.
- Documented `KalshiTieredFeeModel` as a conservative upper-bound approximation.
- Added fee expected-value tests and direction venue-name regression.
- Regenerated `docs/sample_report/relative_value_candidates.md`.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `Copy-Item reports\relative_value_candidates.md docs\sample_report\relative_value_candidates.md`

Tests run:

- `python -m pytest -q`: 52 passed in 0.21s

What works:

- Default fixture scan now suppresses redundant opposite-side sportsbook rows.
- `python scan.py` produced 7 candidates and 0 `POSSIBLE_ARB`.
- Sportsbook-vs-exchange rows show `quote_freshness_unverified`.
- `include_ignore=True` still exposes opposite-side/debug candidates.

What remains stubbed:

- All adapters are fixture-only.
- No live quote freshness against wall-clock `now()`.
- Fee model remains conservative until venue-specific fees are wired in.
- No account connections, order routing, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py
```

## 2026-05-19 Live Read-Only Polymarket Ingestion

Completed:

- Added `python scan.py fetch-polymarket` as an explicit live read-only Polymarket Gamma discovery command.
- Added a public Gamma client with timeout, user-agent, defensive response parsing, and clear failure messages.
- Added raw-plus-normalized snapshot output at `reports/polymarket_markets_snapshot.json`.
- Filtered normalized live rows by default: active, not closed, not archived, accepting orders when present, and not clearly past end date.
- Added debug flags: `--include-closed`, `--include-not-accepting-orders`, and `--include-past-end-date`.
- Added skip counters for closed, not accepting orders, inactive, archived, and past-end-date markets.
- Kept live Polymarket data out of scanner scoring; live fetch does not compare against Kalshi and cannot emit `POSSIBLE_ARB`.
- Added mocked tests for Gamma event parsing, market extraction, outcome/outcomePrices mapping, missing prices, snapshot counts, and CLI success/failure.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json`

Tests run:

- `python -m pytest -q`: 62 passed

What works:

- Fixture scan remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Live Polymarket discovery fetched 25 events, 106 raw markets, normalized 34 live/useful markets, and wrote the ignored snapshot JSON.
- Latest skip counts: closed 54, not accepting orders 54, inactive 1, archived 0, past end date 65.
- Tests use mocked clients/fixture responses only; no network is required for tests.

What remains stubbed:

- No Kalshi live read-only snapshot.
- No live cross-venue matching.
- No CLOB orderbook depth normalization beyond Gamma discovery fields.
- No account connections, authentication, order routing, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json
```

## 2026-05-19 Polymarket Ingestion Hardening

Completed:

- Renamed per-outcome normalized field from `yes_probability` to `outcome_yes_token_price`.
- Added Gamma discovery `best_bid` and `best_ask` fields to normalized market rows.
- Removed duplicate top-level `raw_market_count`; `market_count` now carries raw market count.
- Made missing `acceptingOrders` fail closed by default; only `acceptingOrders=true` passes unless `--include-not-accepting-orders` is used.
- Added client tests for HTTP, URL, timeout, invalid JSON, URL/header contract, wrapper parsing, naive timestamps, inactive/archived markets, and trailing outcomes with missing prices.
- Added skip-counter overlap notes to CLI output and docs.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json`

Tests run:

- `python -m pytest -q`: 77 passed

What works:

- Fixture scan remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Live Polymarket discovery fetched 25 events, 106 raw markets, normalized 34 live/useful markets, and wrote the ignored snapshot JSON.
- Latest skip counts: closed 54, not accepting orders 55, inactive 1, archived 0, past end date 65. Skip counters can overlap.
- First normalized live row was active, not closed, accepting orders, had `best_bid`/`best_ask`, and used `outcome_yes_token_price`.

What remains stubbed:

- No Kalshi live read-only snapshot.
- No live cross-venue matching.
- Gamma `best_bid`/`best_ask` are discovery numbers only, not normalized orderbook depth.
- No account connections, authentication, order routing, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json
```

## 2026-05-20 Live Read-Only Kalshi Ingestion

Completed:

- Added `python scan.py fetch-kalshi` as an explicit live read-only Kalshi market snapshot command.
- Added a public Kalshi `GET /markets?status=open` client with timeout, user-agent, defensive parsing, and clear failure messages.
- Added `schema_version=1` raw-plus-normalized snapshot output at `reports/kalshi_markets_snapshot.json`.
- Filtered normalized rows by default to live/useful markets: `status=open` or `status=active`, not closed/settled/expired, and not clearly past close time.
- Added debug flags: `--include-closed` and `--include-past-close-time`.
- Added skip counters for closed, inactive, and past-close-time markets.
- Kept live Kalshi data out of scanner scoring; live fetch does not compare against Polymarket and cannot emit `POSSIBLE_ARB`.
- Added mocked tests for response parsing, URL/header construction, closed/settled/expired filtering, `status=active`, past close time, schema version, CLI success/failure, and no-network behavior.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py fetch-kalshi --limit 25 --output reports\kalshi_markets_snapshot.json`

Tests run:

- `python -m pytest -q`: 89 passed

What works:

- Fixture scan remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Live Kalshi discovery fetched 25 markets, normalized 25 live/useful markets, and wrote the ignored snapshot JSON.
- Latest skip counts: closed 0, inactive 0, past close time 0. Skip counters can overlap.
- First normalized live row had `schema_version=1`, `status=active`, future close time, YES `best_bid`/`best_ask`, liquidity dollars, and outcome token prices.

What remains stubbed:

- No live cross-venue matching.
- No Kalshi orderbook/depth endpoint use.
- Kalshi `best_bid`/`best_ask` are market metadata discovery values only, not normalized orderbook depth.
- No account connections, authentication, order routing, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py fetch-kalshi --limit 25 --output reports\kalshi_markets_snapshot.json
```

## 2026-05-20 Snapshot Schema V1 Alignment

Completed:

- Added `schema_version=1` to Polymarket live discovery snapshots.
- Confirmed Kalshi live discovery snapshots already use `schema_version=1`.
- Added/updated tests so both live fetch CLI paths assert `schema_version == 1`.
- Documented the shared snapshot top-level fields and common normalized market fields.
- Documented that venue-specific fields are allowed, but consumers should depend only on common fields unless explicitly handling a venue-specific field.
- Did not add live cross-venue matching or feed live snapshots into scoring.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json`
- `python scan.py fetch-kalshi --limit 25 --output reports\kalshi_markets_snapshot.json`
- `git status --short`
- `git diff --stat`

Tests run:

- `python -m pytest -q`: 89 passed in 0.51s

What works:

- Snapshot schema-v1 contract is now consistent across live Kalshi and Polymarket discovery outputs.
- `python scan.py` remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Latest Polymarket fetch: 25 events, 106 markets, 34 normalized; skipped closed 54, not accepting orders 55, inactive 1, archived 0, past end date 65.
- Latest Kalshi fetch: 25 markets, 25 normalized; skipped closed 0, inactive 0, past close time 0.
- Live snapshots remain discovery-only and are not scoring inputs.

What remains stubbed:

- No live cross-venue matching.
- No orderbook/depth normalization.
- No account connections, authentication, order routing, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json
```

## 2026-05-20 Live Snapshot Matching Prototype

Completed:

- Added `relative_value/live_snapshot_matcher.py` as a saved-file-only schema-v1 snapshot matcher.
- Added `python scan.py match-live-snapshots --polymarket ... --kalshi ... --output ...`.
- Added `docs/LIVE_SNAPSHOT_MATCHING.md` explaining the approach, limits, and non-scoring/non-trading boundary.
- Matcher emits only `WATCH` or `MANUAL_REVIEW` pairs; it never emits `POSSIBLE_ARB`.
- Matcher does not call live APIs and does not feed live snapshots into `RelativeValueScanner`.
- Added tests for schema-v1 loading, missing/unsupported schema versions, closed/inactive ineligibility, weak-match suppression, and CLI output.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json`

Tests run:

- `python -m pytest -q`: 94 passed in 0.40s

What works:

- `python scan.py` remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Latest matcher run on saved live snapshots wrote `reports/live_snapshot_pairs.json` with 0 pairs and no actions.
- Saved schema-v1 snapshot files can be matched into manual-review pairs.
- Weak text matches are suppressed.
- Closed/inactive markets are marked with ineligibility reasons.
- Snapshot/schema/freshness issues are surfaced as review blockers.

What remains stubbed:

- No live API calls in matching.
- No live cross-venue scoring.
- No settlement-rule equivalence proof.
- No orderbook/depth normalization.
- No account connections, authentication, order routing, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
```

## 2026-05-20 Targeted Fetch Controls

Completed:

- Added optional Polymarket Gamma discovery filters: `--tag-slug` and `--tag-id`.
- Added optional Kalshi public markets discovery filters: `--series-ticker` and `--event-ticker`.
- Added Kalshi pagination controls: `--cursor` and `--max-pages`, following returned `cursor` or `next_cursor` values only when more than one page is explicitly requested.
- Preserved default broad fetch behavior when targeted flags are omitted.
- Preserved `schema_version=1` snapshot output for both venues.
- Added mocked URL/client-construction tests for targeted query params, unchanged defaults, Kalshi cursor following, CLI wiring, and schema preservation.
- Kept fetch paths read-only: no auth, account, order, position, balance, wallet, CLOB execution, or private-key logic.

Commands run:

- `python -m pytest tests\test_kalshi_live.py tests\test_polymarket_live.py -q`
- `python -m pytest -q`
- `python scan.py`
- `python scan.py fetch-polymarket --tag-slug nba --limit 50 --output reports\polymarket_markets_snapshot.json`
- `python scan.py fetch-kalshi --series-ticker KXNBA --limit 50 --max-pages 2 --output reports\kalshi_markets_snapshot.json`
- `python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json`
- `python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json`

Tests run:

- `python -m pytest tests\test_kalshi_live.py tests\test_polymarket_live.py -q`: 41 passed in 0.41s
- `python -m pytest -q`: 140 passed in 0.58s

Targeted fetch examples:

```powershell
python scan.py fetch-polymarket --tag-slug nba --limit 50 --output reports\polymarket_markets_snapshot.json
python scan.py fetch-polymarket --tag-id 100381 --limit 50 --output reports\polymarket_markets_snapshot.json
python scan.py fetch-kalshi --series-ticker KXNBA --limit 50 --max-pages 2 --output reports\kalshi_markets_snapshot.json
python scan.py fetch-kalshi --event-ticker KXNBA-26MAY20 --limit 50 --output reports\kalshi_markets_snapshot.json
```

What works:

- Targeted flags are included in public discovery query params only when supplied.
- Default fetch URL contracts remain unchanged: Polymarket still uses `active=true&closed=false&limit=...`; Kalshi still uses `status=open&limit=...`.
- Kalshi multi-page fetches merge market rows into the same schema-v1 snapshot shape while retaining raw pages for audit.
- Targeted NBA/KXNBA live discovery wrote fresh schema-v1 snapshots: Polymarket 41 events, 982 markets, 576 normalized; Kalshi 4 markets, 4 normalized.
- Targeted NBA/KXNBA snapshot matching wrote 4 `MANUAL_REVIEW` pairs, so this targeted snapshot set is no longer pair-starved.
- Paper candidate evaluation on those pairs wrote 4 `WATCH` rows and 0 `PAPER_CANDIDATE` rows because the existing enriched snapshots did not contain matching enriched markets for the new targeted pair IDs.

What remains intentionally not built:

- No live scoring from targeted snapshots.
- No matcher promotion to `PAPER_CANDIDATE`, `PAPER`, or `POSSIBLE_ARB`.
- No automatic orderbook enrichment refresh after targeted discovery.
- No trading, authentication, account, order, balance, position, wallet, private-key, database, scheduler, or dashboard logic.

Next exact command:

```powershell
python scan.py enrich-orderbooks --snapshot reports\polymarket_markets_snapshot.json --venue polymarket --output reports\polymarket_orderbook_enriched_snapshot.json
python scan.py enrich-orderbooks --snapshot reports\kalshi_markets_snapshot.json --venue kalshi --output reports\kalshi_orderbook_enriched_snapshot.json
python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json
```

## 2026-05-20 Read-Only Markout Replay

Completed:

- Added `relative_value/markout_replay.py` for saved-file-only paper candidate markout replay.
- Added `python scan.py replay-paper-candidate-markouts --ledger ... --polymarket-enriched-later ... --kalshi-enriched-later ... --output ...`.
- Added `docs/MARKOUT_REPLAY.md`.
- Replay matches by Polymarket `market_id` and Kalshi `ticker`.
- Replay uses the original ledger bid/ask direction: `BUY_YES` uses later best ask and `SELL_YES` uses later best bid.
- Replay reuses the evaluator fee defaults: Polymarket no-fee placeholder and Kalshi conservative tiered estimate.
- Replay never uses midpoint prices, never assumes fills, never walks books, and never calls live APIs.
- Missing, stale, too-early, or too-late windows stay null with `markout_status`.
- Added local JSON tests for filled windows, null windows, no-midpoint behavior, missing markets, stale quotes, fee logic, disallowed action guardrails, input non-mutation, and CLI wiring.

Commands run:

- `python -m pytest tests\test_markout_replay.py -q`
- `python -m pytest -q`
- `python scan.py`
- `python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger_marked.json`

Tests run:

- `python -m pytest tests\test_markout_replay.py -q`: 10 passed in 0.25s
- `python -m pytest -q`: 150 passed in 0.66s

What works:

- Markout windows can be filled from later saved enriched snapshots when timestamps are within tolerance.
- Filled rows include later venue quote times, bid/ask quotes, bid/ask gross gap, per-leg fees, estimated net gap, change in estimated net gap, and `spread_closed_boolean`.
- A spread closing remains research evidence only; it is not a profit claim or an executable-liquidity claim.
- Current same-file replay wrote `reports\paper_candidates_ledger_marked.json` with 4 candidates and 16 windows: 0 filled, 12 `no_data`, 4 `stale`, 0 `missing_market`, and 0 `missing_orderbook`.

What remains intentionally not built:

- No live markout fetching.
- No P&L, position tracking, fill simulation, book walking, capital allocation, database, scheduler, dashboard, settlement proof, or trading logic.
- No `PAPER` or `POSSIBLE_ARB` output.

Next exact command:

```powershell
python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger_marked.json
```

## 2026-05-20 Live Snapshot Matcher Precision Aids

Completed:

- Added saved-file-only settlement-time agreement and event/league keyword boosts to `relative_value/live_snapshot_matcher.py`.
- Added review metadata fields for question similarity, event-title similarity, settlement-time delta/bonus/warning, shared event tokens, event keyword bonus, and final similarity.
- Added local JSON tests proving disjoint snapshots still produce 0 pairs, close settlement times can help reasonable text matches, shared event tokens can help reasonable text matches, shared tokens alone cannot force weak matches, bad times do not crash or get a bonus, and matcher actions remain only `WATCH`/`MANUAL_REVIEW`.

Commands run:

- `python -m pytest tests\test_live_snapshot_matcher.py -q`
- `python -m pytest -q`
- `python scan.py`
- `python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json`
- `python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json`

Tests run:

- `python -m pytest tests\test_live_snapshot_matcher.py -q`: 13 passed in 0.27s
- `python -m pytest -q`: 133 passed in 0.60s

What works:

- `python scan.py` remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Matcher output remains saved-file-only and action-limited to `WATCH`/`MANUAL_REVIEW`.
- Current local live snapshots still produced 0 pairs, so the paper candidate ledger wrote 0 candidates and 0 `PAPER_CANDIDATE` rows.

What remains limited:

- Settlement-time proximity and shared event/league tokens are matching aids only, not settlement-rule proof.
- The matcher still does not score, trade, enrich orderbooks, compute fees, or emit `PAPER_CANDIDATE`, `PAPER`, or `POSSIBLE_ARB`.

Next exact command:

```powershell
python scan.py fetch-polymarket --limit 100 --output reports\polymarket_markets_snapshot.json
python scan.py fetch-kalshi --limit 100 --output reports\kalshi_markets_snapshot.json
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
```

## 2026-05-20 Live Snapshot Matcher Review Fixes

Completed:

- Removed the unconditional `*_liquidity_units_unverified` gating reason so clean liquid pairs can reach `MANUAL_REVIEW`.
- Kept `*_missing_liquidity_units` as an ineligibility reason when liquidity is actually missing.
- Changed similarity to use `min(question_score, event_score)` when both event titles are available.
- Added `--min-similarity` and `--max-snapshot-age-hours` CLI flags.
- Updated `docs/LIVE_SNAPSHOT_MATCHING.md` to document prefixed reason names.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json`

Tests run:

- `python -m pytest -q`: 96 passed in 0.46s

What works:

- `python scan.py` remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Latest matcher run on saved live snapshots wrote `reports/live_snapshot_pairs.json` with 0 pairs and no actions.
- `MANUAL_REVIEW` is reachable in tests for clean pairs with non-null liquidity on both venues.
- Event title mismatch can suppress a pair even when questions are highly similar.

What remains stubbed:

- No live API calls in matching.
- No live cross-venue scoring.
- No settlement-rule equivalence proof.
- No orderbook/depth normalization.

Next exact command:

```powershell
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
```

## 2026-05-20 Read-Only Orderbook Enrichment

Completed:

- Added `venues/orderbooks.py` with read-only Kalshi and Polymarket orderbook clients plus depth parsers.
- Added `relative_value/orderbook_enrichment.py` to enrich saved schema-v1 snapshots without scoring or matching.
- Added `python scan.py enrich-orderbooks --snapshot ... --venue ... --output ...`.
- Added `docs/ORDERBOOK_ENRICHMENT.md`.
- Added mocked tests for Kalshi YES/NO book mechanics, Polymarket token orderbooks, missing token ids, stale snapshots, HTTP failures, and CLI wiring.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py enrich-orderbooks --snapshot reports/kalshi_markets_snapshot.json --venue kalshi --output reports/kalshi_orderbook_enriched_snapshot.json`
- `python scan.py enrich-orderbooks --snapshot reports/polymarket_markets_snapshot.json --venue polymarket --output reports/polymarket_orderbook_enriched_snapshot.json`

Tests run:

- `python -m pytest -q`: 106 passed in 0.53s

What works:

- `python scan.py` remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.
- Kalshi enrichment command wrote `reports/kalshi_orderbook_enriched_snapshot.json`: 25 markets, 0 enriched, 25 unenriched because sampled orderbooks returned empty/unavailable books.
- Polymarket enrichment command wrote `reports/polymarket_orderbook_enriched_snapshot.json`: 34 markets, 34 enriched, 0 unenriched.
- Enriched rows include `orderbook_captured_at`, best bid/ask, spread, depth-at-best, depth within 1c/3c/5c, endpoint, status, and warnings.

What remains stubbed:

- Enriched snapshots are not fed into `RelativeValueScanner` scoring.
- Enriched snapshots are not fed into live snapshot matching.
- No fee/slippage/fillability or profit claim is made from depth fields.
- No authentication, accounts, order placement, order cancellation, database, scheduler, or dashboard.

Next exact command:

```powershell
python scan.py enrich-orderbooks --snapshot reports\polymarket_markets_snapshot.json --venue polymarket --output reports\polymarket_orderbook_enriched_snapshot.json
```

## 2026-05-20 Read-Only Paper Candidate Evaluator

Completed:

- Added `relative_value/paper_candidate_evaluator.py` as a saved-JSON-only ledger evaluator.
- Added `python scan.py evaluate-paper-candidates --pairs ... --polymarket-enriched ... --kalshi-enriched ... --output ...`.
- Added `docs/PAPER_CANDIDATE_LEDGER.md`.
- Added local JSON tests for schema gates, enriched joins, stale quotes, bid/ask-only gaps, fees, depth, settlement-time caps, matcher reason propagation, unit mismatch, CLI success/failure, and input non-mutation.
- Kept `relative_value/scoring.py`, `matching.py`, `scanner.py`, `config.py`, `models.py`, `fees.py`, `live_snapshot_matcher.py`, `orderbook_enrichment.py`, and venue modules unchanged.
- Housekeeping after review: split default fees by venue (`NoFeeModel` for Polymarket, `KalshiTieredFeeModel` for Kalshi), set the CLI saved-file freshness default to 1800 seconds, added venue-prefixed enrichment warning propagation, and clarified `--accept-unit-mismatch` help text.

Commands run:

- `python -m pytest -q`
- `python scan.py`
- `python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json`

Tests run:

- `python -m pytest -q`: 127 passed in 0.53s

What works:

- Evaluator emits only `WATCH`, `MANUAL_REVIEW`, and `PAPER_CANDIDATE`.
- Default unit mismatch cap keeps otherwise clean positive gaps at `MANUAL_REVIEW`; `--accept-unit-mismatch` is required for `PAPER_CANDIDATE`.
- Gross gap uses bid/ask only and subtracts venue-specific per-leg fee estimates.
- Unenriched rows propagate source `enrichment_warnings` into ledger `ineligibility_reasons` with venue prefixes.
- Current local reports had 0 matched live snapshot pairs, so the ledger command wrote 0 candidates.
- `python scan.py` remains unchanged: 7 candidates and 0 `POSSIBLE_ARB`.

What remains stubbed:

- No live API calls inside the evaluator.
- No actual markout computation; all markout windows are null placeholders.
- No position tracking, P&L, capital allocation, book walking, unit reconciliation, database, scheduler, dashboard, or settlement-rule proof.

Next exact command:

```powershell
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
```
