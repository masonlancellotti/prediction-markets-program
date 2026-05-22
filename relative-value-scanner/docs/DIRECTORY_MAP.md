# Directory Map

## Root Files

- `scan.py`: CLI for the default fixture scan plus explicit `fetch-polymarket`, `fetch-kalshi`, `fetch-the-odds-api`, `match-live-snapshots`, `enrich-orderbooks`, `evaluate-paper-candidates`, `replay-paper-candidate-markouts`, and `run-targeted-pipeline` read-only commands.
- `README.md`: quick-start and action ladder.
- `requirements.txt`: test dependency list.
- `.env.example`: documents that this scaffold is offline/read-only.
- `.gitignore`: ignores local env, caches, and generated reports.

## `relative_value/`

- `models.py`: dataclasses, action/source enums, timezone-aware timestamps, and top-of-book contract liquidity.
- `config.py`: conservative scanner thresholds.
- `normalize.py`: text and datetime normalization helpers.
- `reference_odds.py`: American odds and no-vig sportsbook conversion.
- `fees.py`: fee model interface plus flat, Kalshi-tiered, and no-fee implementations.
- `contract_relationship.py`: deterministic contract-relationship classification constants and report shape for review/debugging only.
- `llm_relationship_classifier.py`: stubbed no-network LLM relationship proposal validator and audit sidecar helpers; review metadata only.
- `source_registry.py`: non-networked source taxonomy and output-policy registry for executable, reference-only, and signal-only sources.
- `matching.py`: match confidence and settlement mismatch risk.
- `scoring.py`: action ladder and POSSIBLE_ARB hard gates.
- `scanner.py`: deterministic pairwise scanner and default suppression of redundant opposite-side sportsbook reference rows.
- `live_snapshot_matcher.py`: read-only schema-v1 snapshot matcher with conservative text, settlement-time, and event-keyword review signals; emits WATCH/MANUAL_REVIEW pairs only.
- `orderbook_enrichment.py`: saved schema-v1 snapshot enrichment coordinator; attaches read-only depth metrics without scoring.
- `paper_candidate_evaluator.py`: saved-JSON-only paper candidate ledger evaluator; emits WATCH/MANUAL_REVIEW/PAPER_CANDIDATE only.
- `markout_replay.py`: saved-file-only paper candidate markout replay; fills research markout windows from later enriched snapshots without fetching, scoring, or trading.
- `report.py`: JSON and Markdown report writers.

## `venues/`

- `base.py`: read-only adapter interface and exchange fixture loader.
- `kalshi.py`: Kalshi fixture adapter plus public read-only market discovery client, targeted `series_ticker`/`event_ticker`/cursor controls, live/useful filters, skip counters, and schema-versioned snapshot normalizer.
- `polymarket.py`: Polymarket fixture adapter plus public read-only Gamma discovery client, targeted `tag_slug`/`tag_id` controls, market filters, overlapping skip counters, and schema-versioned snapshot normalizer with `outcome_yes_token_price` plus Gamma `best_bid`/`best_ask` fields.
- `orderbooks.py`: public read-only Kalshi and Polymarket orderbook clients plus depth metric parsers.
- `the_odds_api.py`: sportsbook fixture adapter plus read-only The Odds API reference snapshot client and no-vig normalization.
- `fixtures/`: offline sample data.

## `tests/`

- `test_models.py`: model validation tests.
- `test_adapters.py`: fixture adapter fail-closed executable tests.
- `test_reference_odds.py`: no-vig conversion tests.
- `test_matching.py`: match-confidence and settlement-risk tests.
- `test_scoring.py`: action ladder and POSSIBLE_ARB gate tests.
- `test_scanner_end_to_end.py`: fixture scan and report generation test.
- `test_live_snapshot_matcher.py`: saved snapshot matching tests with local JSON only, including precision-aid guardrails.
- `test_kalshi_live.py`: mocked Kalshi live discovery parsing, targeted URL construction, cursor following, schema, filters, and CLI tests.
- `test_polymarket_live.py`: mocked Polymarket Gamma parsing, targeted tag URL construction, schema, filters, HTTP failure, and CLI tests.
- `test_orderbook_enrichment.py`: mocked orderbook parser, client, failure-mode, and CLI tests.
- `test_paper_candidate_evaluator.py`: local JSON evaluator tests for gates, fee subtraction, unit mismatch cap, ledger shape, CLI success/failure, and no-midpoint behavior.
- `test_markout_replay.py`: local JSON markout replay tests for window filling, no-midpoint logic, stale/missing statuses, fee reuse, no disallowed actions, input non-mutation, and CLI wiring.
- `test_source_registry.py`: source taxonomy tests proving executable, reference-only, signal-only, planned, and unknown-source behavior.
- `test_the_odds_api_live.py`: mocked The Odds API reference snapshot tests; no real API key or network required.
- `test_llm_relationship_classifier.py`: strict LLM proposal schema, forbidden-output, audit sidecar, and no-behavior-change tests.

## `reports/`

Generated scan outputs:

- `relative_value_candidates.json`
- `relative_value_candidates.md`
- `polymarket_markets_snapshot.json`
- `kalshi_markets_snapshot.json`
- `live_snapshot_pairs.json`
- `kalshi_orderbook_enriched_snapshot.json`
- `polymarket_orderbook_enriched_snapshot.json`
- `paper_candidates_ledger.json`
- `paper_candidates_ledger_marked.json`
- `the_odds_api_reference_snapshot.json`

These report files are generated artifacts and are ignored by `.gitignore`.

## `docs/sample_report/`

- `relative_value_candidates.md`: durable example of the report shape after quote freshness and liquidity-unit hardening.

## `docs/LIVE_SNAPSHOT_MATCHING.md`

Design note for the read-only live snapshot matcher prototype and its limits.

## `docs/ORDERBOOK_ENRICHMENT.md`

Design note for the read-only orderbook/depth enrichment layer and its limits.

## `docs/PAPER_CANDIDATE_LEDGER.md`

Design note for saved-file paper-candidate review ledgers and their limits.

## `docs/SOURCE_TAXONOMY.md`

Design note for source types, planned source entries, and source-specific output policy.

## `docs/MARKOUT_REPLAY.md`

Design note for saved-file-only markout replay and its research-only limits.
