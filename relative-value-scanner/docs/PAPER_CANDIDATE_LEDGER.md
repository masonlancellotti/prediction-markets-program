# Paper Candidate Ledger

## Purpose

`evaluate-paper-candidates` consumes saved schema-v1 JSON files only:

- `reports/live_snapshot_pairs.json`
- `reports/polymarket_orderbook_enriched_snapshot.json`
- `reports/kalshi_orderbook_enriched_snapshot.json`

It produces `reports/paper_candidates_ledger.json` for manual paper-candidate review. It does not fetch live data, place orders, score with `RelativeValueScanner`, or emit `PAPER` / `POSSIBLE_ARB`.

## Command

```powershell
python scan.py evaluate-paper-candidates --pairs reports\live_snapshot_pairs.json --polymarket-enriched reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger.json
```

Use `--accept-unit-mismatch` only when the operator explicitly accepts the unresolved Polymarket-shares versus Kalshi-contracts unit mismatch. Without that flag, otherwise clean positive gaps are capped at `MANUAL_REVIEW`. The flag is not sufficient by itself: otherwise clean rows must also carry an existing proven same-payoff relationship object from the trusted board write-back workflow before they can reach `PAPER_CANDIDATE`.

For the current MLB World Series workflow, use the explicit paper-check runner to keep quote freshness inside the review window:

```powershell
python scan.py run-mlb-world-series-paper-check --polymarket-snapshot reports\mlb_kxmlb_48h_unitok_after_guardrails_polymarket_snapshot.json --kalshi-snapshot reports\mlb_kxmlb_48h_unitok_after_guardrails_kalshi_snapshot.json --pairs reports\mlb_world_series_pairs.json --accept-unit-mismatch --trust-settlement-normalization mlb_world_series_timezone_convention_drift
```

The runner performs read-only orderbook enrichment for both saved snapshots, builds the same-payoff board, writes derived pairs with `same_payoff_board_v1` evidence, evaluates the existing paper gates, and writes a compact JSON/Markdown summary. It does not lower thresholds or change evaluator gates. If `PAPER_CANDIDATE` appears, it prints `STOP_AND_REVIEW` and performs no further action.

## Freshness vs. Saved-File Workflow

The CLI default `--max-quote-age-seconds` is `1800` because this command evaluates saved snapshots that may have been fetched and enriched minutes earlier. Direct library use remains strict by default. Operators should tighten this value for near-real-time review or raise it only when deliberately inspecting older saved files.

## Fees

Fees are split by venue. The Polymarket leg defaults to `NoFeeModel()` because venue-specific fee wiring is not modeled here yet. The Kalshi leg defaults to `KalshiTieredFeeModel()`, a conservative upper-bound estimate. Both fees are recorded separately in the ledger and subtracted from the bid/ask gross gap.

## Actions

The evaluator emits only:

- `WATCH`
- `MANUAL_REVIEW`
- `PAPER_CANDIDATE`

It never emits `PAPER` or `POSSIBLE_ARB`. Those belong to the fixture/offline `RelativeValueScanner` path and remain untouched.

## Gates

Promotion is deterministic and conservative:

- all inputs must have `schema_version=1`
- matched pairs join to enriched snapshots by Polymarket `market_id` and Kalshi `ticker`
- both orderbooks must be `enriched`
- bid/ask fields must be present
- orderbook captures must be timezone-aware and fresh
- top-of-book depth on the hit side must meet the minimum
- settlement end/close times must be timezone-aware and close enough
- matcher ineligibility reasons are propagated; `ambiguous_wording` caps at `MANUAL_REVIEW`
- sportsbook/reference rows are forced to `reference_only_watch`
- gross gap uses only bid/ask, never midpoint
- per-venue, per-leg fee estimates are subtracted
- unit mismatch warning is always emitted
- `--accept-unit-mismatch` still requires an existing proven same-payoff relationship object before `PAPER_CANDIDATE`
- trusted same-payoff relationship source must be allowlisted as `same_payoff_board_v1`
- trusted evidence must include `same_payoff_board_evidence.classifier_version=same-payoff-board-v1`
- trusted evidence must have `strict_pass_count == strict_comparator_count`

## Contract Relationship

Ledger rows include a deterministic `contract_relationship` object re-classified from matcher relationship-level blocking reasons and, where relevant, the unresolved Polymarket-shares versus Kalshi-contracts unit warning. The evaluator does not trust free-form reason strings or arbitrary sources for promotion. For any otherwise clean row where `--accept-unit-mismatch` is supplied, the input pair must already include:

- `relationship == EQUIVALENT`
- `same_payoff == true`
- `blocking_reasons == []`
- `source == same_payoff_board_v1`
- `same_payoff_board_evidence.classifier_version == same-payoff-board-v1`
- `same_payoff_board_evidence.strict_pass_count == strict_comparator_count`

Otherwise the row remains `MANUAL_REVIEW` with `relationship_same_payoff_not_proven`.

This relationship layer is not trade permission. Semantic similarity is not settlement equivalence, sportsbook/reference odds are not executable prices, and a future LLM reviewer may help classify contracts but cannot approve candidates by itself.

## Markout Replay

The evaluator initializes `t_plus_30s`, `t_plus_5m`, `t_plus_30m`, and `t_plus_2h` markouts as null placeholders. Use `replay-paper-candidate-markouts` to fill those windows from later saved enriched snapshots:

```powershell
python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot_later.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot_later.json --output reports\paper_candidates_ledger_marked.json
```

Replay matches rows by Polymarket `market_id` and Kalshi `ticker`, uses the original ledger's bid/ask direction, and applies the same evaluator fee defaults. It fills only windows where both later saved quote timestamps are within tolerance. Missing, stale, too-early, or too-late windows remain null with `markout_status`.

Markouts are research evidence only. A spread closing is not guaranteed profit, no midpoint fills are used, no execution is assumed, and no settlement-equivalence or executable-liquidity claim is made.

## Ledger Limits

Markouts remain null unless a saved-snapshot replay pass fills them. Null windows are intentional when the saved later data is missing, stale, too early, or too late.

The ledger is not P&L, position tracking, capital allocation, slippage modeling, settlement-rule proof, book walking, or a trading instruction.
