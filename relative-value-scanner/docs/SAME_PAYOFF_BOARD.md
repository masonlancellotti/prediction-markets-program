# Same-Payoff Candidate Board

## Purpose

`same-payoff-board` is a saved-file diagnostic report for Kalshi x Polymarket market pairs. It surfaces pairs that look structurally close to exact same-payoff and explains, comparator by comparator, why each row passes or fails strict relationship checks.

It is not a matcher, evaluator, execution engine, or trading signal. It does not create paper candidates and does not override `contract_relationship`.

## Command

```powershell
python scan.py same-payoff-board --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --json-output reports\same_payoff_candidate_board.json --markdown-output reports\same_payoff_candidate_board.md
```

## Inputs

The command reads saved JSON files only:

- matcher pairs JSON, for example `reports/live_snapshot_pairs.json`
- orderbook-enriched Polymarket snapshot
- orderbook-enriched Kalshi snapshot

The enriched snapshot inputs are expected to contain normalized markets plus `orderbook_enrichment` blocks. Raw live-readonly snapshots are not the intended inputs because the board also reports quote/depth freshness and enrichment availability as diagnostic context.

## Strict Comparators

Strict comparators determine whether the board row can set `same_payoff=true` inside the board report. Any strict comparator failure keeps board `same_payoff=false`.

- reference-only blocker
- normalized settlement/source string
- end date / close time agreement within tolerance
- market/event entity agreement
- sport/league/team agreement when sports terms are detected
- threshold / strike agreement
- market type agreement
- outcome direction / polarity agreement
- side-definition tokens
- structural relationship shape checks, such as unrelated teams, subset/superset thresholds, or overlap-not-equivalent IPO timing
- unit / liquidity-unit mismatch

Board `same_payoff=true` is diagnostic evidence only. It is not trade permission and does not mutate any matcher or evaluator output.

## Derived Evidence Write-Back

The board can be attached to matcher pairs only through a derived-file workflow:

```powershell
python scan.py attach-same-payoff-evidence --pairs reports\live_snapshot_pairs.json --board reports\same_payoff_candidate_board.json --output reports\live_snapshot_pairs_with_same_payoff_evidence.json
```

This command reads saved JSON files only and writes a new matcher-pairs JSON. It does not mutate the original pairs file. Rows are joined by stable pair identity, currently Polymarket `market_id` and Kalshi `ticker` / `market_ticker` / `market_id`. Ambiguous or missing identities fail closed and do not promote.

Only board rows with `same_payoff=true`, all strict comparators passing, no blockers, no missing strict fields, and an executable Kalshi x Polymarket leg check can replace the derived pair's `contract_relationship` with trusted `source=same_payoff_board_v1` evidence.

## Info-Only Comparators

Info-only comparators add operational blockers and missing-field diagnostics but do not prove relationship equivalence:

- Polymarket quote/depth availability and quote age
- Kalshi quote/depth availability and quote age
- fee availability

These fields help decide whether a pair needs better saved enrichment before review. They do not grant paper or live readiness.

## Blocker Taxonomy

Common blockers include:

- `polymarket_not_executable_kalshi_polymarket_leg`
- `kalshi_not_executable_kalshi_polymarket_leg`
- `settlement_source_mismatch`
- `settlement_rule_tiebreak_mismatch`
- `settlement_date_drift`
- `market_event_entity_mismatch`
- `sports_league_team_mismatch`
- `threshold_strike_mismatch`
- `market_type_mismatch`
- `outcome_direction_polarity_mismatch`
- `outcome_direction_ambiguous`
- `no_side_spread_or_side_definition_ambiguous`
- `side_definition_tokens_ambiguous`
- `relationship_shape_unrelated`
- `relationship_shape_subset_or_superset`
- `relationship_shape_overlap_not_equivalent`
- `unit_or_liquidity_unit_mismatch`
- quote/depth blockers such as `polymarket_stale_quote`, `kalshi_stale_quote`, or orderbook-not-enriched blockers

Blockers are meant to be review explanations, not action labels.

## Recommended Next Action

The board emits only diagnostic recommendations:

- `SKIP`: a hard structural blocker exists.
- `BETTER_SOURCE_TARGETING`: the pair likely came from a weak or wrong source universe.
- `RELATIONSHIP_REVIEW`: strict structural checks passed and the pair may deserve human relationship review.
- `ENRICH_IF_APPROVED`: strict relationship checks passed but saved quote/depth context needs refreshed enrichment before further review.
- `WATCH_ONLY`: missing fields prevent stronger review.

These are not execution instructions.

## Relationship to Evaluator Tightening

`evaluate-paper-candidates --accept-unit-mismatch` is not enough to promote a row. Even when the unresolved Polymarket-shares versus Kalshi-contracts unit mismatch is explicitly accepted, the evaluator still requires an existing proven same-payoff relationship object with:

- `relationship=EQUIVALENT`
- `same_payoff=true`
- no relationship blockers

The board can help collect deterministic evidence for future human review. The derived write-back command can write a trusted relationship object only when the strict board evidence fully clears; the evaluator still applies freshness, depth, fee, settlement, unit-acknowledgement, and source-allowlist gates before any `PAPER_CANDIDATE` action.

## Non-Goals

The board does not:

- call live APIs
- call an LLM
- scrape, automate a browser, bypass access controls, or use proxy/VPN/Tor flows
- authenticate, access accounts, balances, positions, wallets, keys, or sessions
- place, cancel, sign, or route orders
- mutate matcher output
- mutate original matcher output
- mutate evaluator output
- use reference-only sources as executable legs
- emit `PAPER_CANDIDATE`, `PAPER`, `POSSIBLE_ARB`, or live readiness
- grant trade permission
