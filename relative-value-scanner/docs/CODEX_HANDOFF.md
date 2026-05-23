# Codex Handoff

## Read First

1. `docs/PROJECT_PURPOSE.md`
2. `docs/DATA_REALITY.md`
3. `docs/MATCHING_RULES.md`
4. `docs/DO_NOT_DO_YET.md`
5. `docs/FALSE_ARB_TRAPS.md`
6. `docs/LIVE_SNAPSHOT_MATCHING.md`
7. `docs/ORDERBOOK_ENRICHMENT.md`
8. `docs/PAPER_CANDIDATE_LEDGER.md`
9. `docs/MARKOUT_REPLAY.md`
10. `docs/CURRENT_STATUS.md`
11. `docs/DIRECTORY_MAP.md`

## Hard Rules

- Work only inside `relative-value-scanner`.
- Do not modify `../kalshi-weather-edge`.
- No live trading.
- No account connections.
- No authenticated trading endpoints.
- No Robinhood private or reverse-engineered APIs.
- No Polymarket or IBKR execution.
- No database, scheduler, or dashboard yet.
- Tests must not require real API keys or network access.
- Sportsbook/reference odds cannot produce `POSSIBLE_ARB`.
- Opposite outcomes, numeric-threshold drift, settlement mismatch, fee underestimation, and NO-side assumptions must fail closed.

## Invariants

- All prices are YES probabilities in `[0, 1]`.
- `liquidity_top_contracts` means top-of-book size in contracts, not dollars or USDC.
- `settlement_time` and `captured_at` must be timezone-aware when present.
- Quotes with stale or unverified freshness cannot reach `PAPER` or `POSSIBLE_ARB`.
- `POSSIBLE_ARB` requires executable exchange-vs-exchange legs, high confidence, low mismatch risk, fresh quotes, positive fee-adjusted gap, and minimum top-of-book contracts.
- Live Polymarket ingestion is read-only public Gamma discovery only; it writes snapshots and does not feed scoring yet.
- Polymarket targeted discovery may pass public Gamma `tag_slug` and/or `tag_id` filters; broad active/not-closed discovery remains unchanged when those filters are omitted.
- Default Polymarket snapshot normalization filters out closed, archived, inactive, not-accepting-orders-or-unknown, and clearly past-end-date markets.
- Polymarket outcome rows use `outcome_yes_token_price`; Gamma `best_bid`/`best_ask` are discovery numbers, not proof of book depth.
- Polymarket skip counters can overlap and are not additive.
- Live Kalshi ingestion is read-only public `GET /markets?status=open` discovery only; it writes `schema_version=1` snapshots and does not feed scoring yet.
- Kalshi targeted discovery may pass public `series_ticker` and/or `event_ticker` filters, start from `cursor`, and follow returned `cursor`/`next_cursor` values with `--max-pages`; broad one-page discovery remains unchanged when those options are omitted.
- Kalshi `status=active` is treated as live/open for discovery because the open-market endpoint returns it in practice.
- Kalshi early-closing markets use parseable `expected_expiration_time` for normalized `end_date` when `can_close_early=true`; normalized `close_time` stays on the conservative close/expiration fallback.
- Kalshi `best_bid`/`best_ask` are YES market metadata values, not normalized orderbook depth.
- Both live snapshot families use `schema_version=1`; consumers should rely only on documented common fields unless explicitly handling venue-specific extras.
- Live snapshot matching reads saved JSON files only and emits `WATCH`/`MANUAL_REVIEW` pairs only.
- Live snapshot matching does not use `RelativeValueScanner`, does not emit `PAPER`, `PAPER_CANDIDATE`, or `POSSIBLE_ARB`, and makes no arb/profit/executable-liquidity claim.
- Live snapshot matching may use close settlement-time proximity and shared event/league keyword bonuses as review aids only; neither is settlement-rule proof.
- Orderbook enrichment reads saved schema-v1 snapshots and public read-only orderbooks only.
- Orderbook enrichment must not feed `RelativeValueScanner` scoring or live snapshot matching until freshness, settlement, fees, slippage, and repeated paper evidence are documented.
- Polymarket orderbook enrichment requires an unambiguous YES token id; missing or ambiguous token ids fail closed as `missing_token_id`.
- Kalshi orderbook enrichment normalizes YES bids and implied YES asks from NO bids; empty books remain `unenriched`.
- Paper candidate evaluation reads saved pairs/enriched snapshots only; it must not call live APIs or mutate input snapshots.
- Paper candidate evaluation emits only `WATCH`, `MANUAL_REVIEW`, or `PAPER_CANDIDATE`; it must never emit `PAPER` or `POSSIBLE_ARB`.
- Paper candidate gaps use bid/ask only, subtract per-leg fees, require fresh enriched orderbooks, and always warn that Polymarket shares and Kalshi contracts are not unit-normalized.
- Paper candidate settlement comparison prefers normalized `end_date`, then falls back to `close_time` only when `end_date` is missing; bad present `end_date` values fail safely.
- Paper candidate fee defaults are split by venue: Polymarket uses `NoFeeModel()`, Kalshi uses `KalshiTieredFeeModel()`.
- The paper candidate CLI defaults to `--max-quote-age-seconds 1800` for saved-file workflows; tighten it when evaluating freshly captured snapshots.
- Markout replay reads an existing paper candidate ledger plus later saved enriched snapshots only; it must not call live APIs or mutate the input ledger.
- Markout replay matches rows by Polymarket `market_id` and Kalshi `ticker`, reuses the original ledger bid/ask direction, and never uses midpoint prices.
- Markout replay is research evidence only. A spread closing is not guaranteed profit, not proof of execution, and not settlement-rule equivalence.
- Missing, stale, too-early, or too-late markout windows stay null with `markout_status`.
- Targeted pipeline runner is orchestration only: it runs read-only discovery, enrichment, saved snapshot matching, and paper candidate evaluation into labeled report files.
- Targeted pipeline runner must not sleep/wait, trade, authenticate, call account/order endpoints, integrate `RelativeValueScanner` scoring, or emit `PAPER`/`POSSIBLE_ARB`.
- Targeted pipeline runner forwards evaluator flags (`--max-settlement-delta-seconds`, `--min-net-gap`, `--min-top-of-book-size`, `--accept-unit-mismatch`) without changing evaluator defaults.

## Current Next Task

Run the explicit MLB World Series paper-check runner against the saved MLB-targeted snapshots and generated WS/WS pairs. Semantic equivalence is solved for the current MLB World Series set; the next blocker diagnosis is execution freshness, depth, and fee-adjusted net gap inside the evaluator.

```powershell
python scan.py run-mlb-world-series-paper-check --polymarket-snapshot reports\mlb_kxmlb_48h_unitok_after_guardrails_polymarket_snapshot.json --kalshi-snapshot reports\mlb_kxmlb_48h_unitok_after_guardrails_kalshi_snapshot.json --pairs reports\mlb_world_series_pairs.json --accept-unit-mismatch --trust-settlement-normalization mlb_world_series_timezone_convention_drift
```

## Last Known Test Command

```powershell
python -m pytest -q
```

Last result: 106 passed.
Current result after paper candidate evaluator housekeeping: 127 passed.
Current result after live snapshot matcher precision aids: 133 passed.
Current result after targeted fetch controls: 140 passed.
Current result after markout replay: 150 passed.
Current focused result after MLB World Series paper-check runner completion: `python -m pytest tests/test_paper_candidate_evaluator.py tests/test_same_payoff_evidence.py tests/test_orderbook_enrichment.py -q` -> 71 passed.

## Last Known Scan Command

```powershell
python scan.py
```

Last result: 7 candidates, 0 `POSSIBLE_ARB`, reports written under `reports/`.

## Last Known Live Polymarket Discovery Command

```powershell
python scan.py fetch-polymarket --limit 25 --output reports\polymarket_markets_snapshot.json
python scan.py fetch-polymarket --tag-slug nba --limit 50 --output reports\polymarket_markets_snapshot.json
```

Last result: 25 events, 106 markets, 34 normalized, 34 orderbook-enabled fields true. Skipped counts: closed 54, not accepting orders 55, inactive 1, archived 0, past end date 65. Skip counters can overlap. Snapshot is generated under ignored `reports/`.
Current targeted NBA result: 41 events, 982 markets, 576 normalized, 576 orderbook-enabled fields true. Skipped counts: closed 95, not accepting orders 95, inactive 310, archived 0, past end date 1. Snapshot is generated under ignored `reports/`.
Targeted controls: optional `--tag-slug` and `--tag-id` are public Gamma discovery filters only; defaults remain unchanged when omitted.

## Last Known Live Kalshi Discovery Command

```powershell
python scan.py fetch-kalshi --limit 25 --output reports\kalshi_markets_snapshot.json
python scan.py fetch-kalshi --series-ticker KXNBA --limit 50 --max-pages 2 --output reports\kalshi_markets_snapshot.json
```

Last result: 25 markets, 25 normalized. Skipped counts: closed 0, inactive 0, past close time 0. Snapshot is generated under ignored `reports/`.
Current targeted NBA result: `--series-ticker KXNBA --limit 50 --max-pages 2` returned 4 markets, 4 normalized. Skipped counts: closed 0, inactive 0, past close time 0. Snapshot is generated under ignored `reports/`.
Targeted controls: optional `--series-ticker`, `--event-ticker`, `--cursor`, and `--max-pages` are public market discovery controls only; defaults remain unchanged when omitted.

## Last Known Live Snapshot Match Command

```powershell
python scan.py match-live-snapshots --polymarket reports\polymarket_markets_snapshot.json --kalshi reports\kalshi_markets_snapshot.json --output reports\live_snapshot_pairs.json
```

Last result after precision aids: 0 pairs on the prior broad saved snapshots, actions none, output written under ignored `reports/`.
Current targeted NBA/KXNBA result: 4 pairs, actions `MANUAL_REVIEW`, output written under ignored `reports/`.

## Last Known Read-Only Orderbook Enrichment Commands

```powershell
python scan.py enrich-orderbooks --snapshot reports\kalshi_markets_snapshot.json --venue kalshi --output reports\kalshi_orderbook_enriched_snapshot.json
python scan.py enrich-orderbooks --snapshot reports\polymarket_markets_snapshot.json --venue polymarket --output reports\polymarket_orderbook_enriched_snapshot.json
```

Last result: Kalshi 25 markets, 0 enriched, 25 unenriched because sampled books were empty/unavailable. Polymarket 34 markets, 34 enriched, 0 unenriched. Outputs are generated under ignored `reports/`.

## Last Known Paper Candidate Evaluation Command

```powershell
python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json --max-quote-age-seconds 1800
```

Last result: saved-file-only ledger command is expected to write under ignored `reports/`; actions are limited to `WATCH`, `MANUAL_REVIEW`, and `PAPER_CANDIDATE`.
Current result after matcher precision aids: 0 candidates because `reports\live_snapshot_pairs.json` contains 0 pairs.
Current targeted NBA/KXNBA result: 4 candidates, all `WATCH`, because the existing enriched snapshots did not contain matching enriched markets for the new targeted pair IDs. `PAPER_CANDIDATE` remains 0.

## Last Known Markout Replay Command

```powershell
python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger_marked.json
```

New command added for saved-file-only replay. It fills markout windows only when later enriched orderbook timestamps are within tolerance of the target window, otherwise values stay null with `markout_status`.
Current same-file replay result: 4 candidates, 16 windows, 0 filled, 12 `no_data`, 4 `stale`, 0 `missing_market`, 0 `missing_orderbook`, output written under ignored `reports/`.

## Last Known Targeted Pipeline Command

```powershell
python scan.py run-targeted-pipeline --polymarket-tag-slug nba --kalshi-series-ticker KXNBA --label nba_kxnba
```

New command added for repeatable read-only targeted workflow. It writes labeled files under `reports/`, prints normalized/enrichment/pair/evaluator summaries, and prints the exact later markout replay command. Tests mock every pipeline step; no network is used in tests.
Current NBA/KXNBA result: Polymarket 600 normalized, Kalshi 4 normalized, Polymarket 528/600 enriched, Kalshi 4/4 enriched, 4 pairs, evaluator counts `WATCH=4`, `MANUAL_REVIEW=0`, `PAPER_CANDIDATE=0`. Top rejection reasons are `settlement_delta_exceeds_limit` and `missed_fill:settlement_delta_exceeds_limit`, both count 4.
Current after evaluator settlement field fix: saved Kalshi KXNBA rows have `end_date=2026-06-30T14:00:00Z` and `close_time=2028-06-29T14:00:00Z`. The normal pipeline still produces 4 `WATCH` rows due to the default one-hour settlement window. The `--max-settlement-delta-seconds 43200` run gets past settlement and still produces 4 `WATCH` rows because two rows have `estimated_net_gap_below_minimum` and two rows have `no_positive_bid_ask_gap`.

Latest printed later markout command:

```powershell
python scan.py replay-paper-candidate-markouts --ledger C:\Users\mason\Downloads\prediction-markets-program\relative-value-scanner\reports\nba_kxnba_paper_candidates.json --polymarket-enriched-later C:\Users\mason\Downloads\prediction-markets-program\relative-value-scanner\reports\nba_kxnba_polymarket_enriched_later.json --kalshi-enriched-later C:\Users\mason\Downloads\prediction-markets-program\relative-value-scanner\reports\nba_kxnba_kalshi_enriched_later.json --output C:\Users\mason\Downloads\prediction-markets-program\relative-value-scanner\reports\nba_kxnba_paper_candidates_marked.json
```
