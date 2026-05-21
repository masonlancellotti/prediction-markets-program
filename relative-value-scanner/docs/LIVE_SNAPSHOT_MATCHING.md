# Live Snapshot Matching Prototype

## Purpose

The live snapshot matcher is a read-only prototype that compares saved schema-v1 Polymarket and Kalshi discovery snapshots and emits tentative cross-venue pairs for human review.

It is not the `RelativeValueScanner`, not strategy scoring, not arb detection, and not trading infrastructure.

## Inputs

The matcher reads local JSON snapshots only:

- `reports/polymarket_markets_snapshot.json`
- `reports/kalshi_markets_snapshot.json`

Alternate paths can be passed with `--polymarket` and `--kalshi`. Tuning flags are `--min-similarity` and `--max-snapshot-age-hours`.

Snapshots must use `schema_version=1`. Missing or unsupported versions are reported as snapshot issues and no pair generation is trusted.

## Matching Approach

The prototype uses documented common schema-v1 fields where possible:

- `event_title`
- `question` or `title`
- `end_date` or `close_time`
- `active` and `closed`
- `liquidity` and `volume` only as metadata

Text matching is intentionally conservative. Weak text matches are not emitted. Strong text overlap only creates a tentative pair; it does not prove settlement equivalence.

The matcher can add small precision aids after question/event text matching:

- A settlement-time bonus when both saved rows have timezone-aware `end_date`/`close_time` values within six hours.
- An event/league keyword bonus when both rows share a fixed keyword such as NBA, MLB, NFL, NHL, MLS, UEFA, election, Senate, House, President, BTC, ETH, Bitcoin, Ethereum, IPO, CPI, Fed, or rates.

Both bonuses require at least reasonable question overlap first. Shared timing or shared event keywords cannot turn a very weak text match into a candidate, and neither signal proves equivalent settlement rules.

Sports futures require competition-scope equivalence. ALCS, NLCS, ALDS, NLDS, AFC/NFC championship, conference final(s), division series, semifinal, wild card, league, conference, Champions League group stage, Champions League round of 16, or Copa America group stage markets are not equivalent to overall championship markets such as World Series, Super Bowl, Stanley Cup, World Cup, MLS Cup, NBA Finals, NHL Finals, Premier League title, La Liga title, Bundesliga title, Serie A title, Champions League title, Copa America, Euro Championship, or generic championship markets. City/team aliases are treated conservatively; for example Los Angeles Dodgers/LAD and Los Angeles Angels/LAA/Los Angeles A mismatches are review blockers.

## Output

The output file is `reports/live_snapshot_pairs.json` by default.

Each pair includes:

- Polymarket market id, question, and event title.
- Kalshi ticker, question, and event title.
- Similarity score.
- Matched fields used for review, including `question_similarity`, `event_title_similarity`, `settlement_time_delta_seconds`, `settlement_time_bonus`, `settlement_time_warning`, `shared_event_tokens`, `event_keyword_bonus`, and `final_similarity_score`.
- Ineligibility reasons.
- `action` limited to `WATCH` or `MANUAL_REVIEW`.

The matcher never emits `PAPER`, `PAPER_CANDIDATE`, or `POSSIBLE_ARB`.

## Ineligibility Reasons

Market-level reasons are prefixed with the venue name, for example `polymarket_missing_close_end_time` or `kalshi_closed_inactive_market`. Snapshot-level reasons are prefixed as `polymarket_snapshot_*` or `kalshi_snapshot_*`.

Reasons include:

- `polymarket_snapshot_missing_schema_version`
- `kalshi_snapshot_unsupported_schema_version`
- `polymarket_missing_close_end_time`
- `kalshi_missing_liquidity_units`
- `ambiguous_wording`
- `polymarket_snapshot_missing_captured_at`
- `kalshi_snapshot_stale_captured_at`
- `polymarket_closed_inactive_market`
- `kalshi_closed_inactive_market`
- `sports_competition_scope_mismatch`
- `sports_team_alias_mismatch`

These are review blockers or caution flags, not trading signals.

## Not Built Yet

- No live API calls.
- No orderbook/depth normalization.
- No settlement-rule equivalence proof.
- No fee modeling.
- No `RelativeValueScanner` integration.
- No paper-candidate promotion.
- No paper/live trading.
- No account, order, balance, position, wallet, or private-key access.
