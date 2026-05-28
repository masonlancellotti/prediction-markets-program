# Structural Basket Dry Run

Saved-file-only pipeline that takes a saved Kalshi market/orderbook snapshot
plus one or more saved Kalshi event-metadata JSON files and walks them
through the full structural-basket review pipeline in one command:

1. `audit_kalshi_event_metadata` — normalize/audit metadata payloads
2. `join_kalshi_event_metadata` — write `enriched_snapshot` with trusted
   venue-native `exhaustive_group` evidence on every member market
3. `build_structural_basket_review_report` — apply explicit fee, depth,
   freshness, settlement, and exhaustiveness gates
4. `simulate_paper_fill_journal` — **only** when at least one
   `STOP_FOR_REVIEW` row exists AND the caller did not pass
   `--skip-paper-fill-simulation`

## What it does not do

- It does not fetch from any URL.
- It does not authenticate, read secrets, or call private endpoints.
- It does not place, cancel, or simulate live orders.
- It does not promote rows to `PAPER_CANDIDATE`.
- It does not use midpoint fills, title similarity, market-graph hints,
  market counts, or per-market Yes/No as exhaustive evidence.
- It does not weaken or override the existing fee, depth, freshness,
  settlement, or exhaustive-evidence gates.

`STOP_FOR_REVIEW` is report/review-only. Surfacing it never authorizes a
real trade.

## First real dry-run command sequence

```powershell
# 1. Save a Kalshi event metadata JSON payload to disk by hand (or via a
#    separately-reviewed read-only acquisition tool). The shape required
#    matches the existing audit normalizer: explicit event-level
#    outcome_list, complete / is_exhaustive / all_outcomes_included
#    markers, rules text, settlement_source evidence, and market_tickers
#    matching the tickers in the saved snapshot.

# 2. Validate it via the saved-file importer and optionally stage it.
python scan.py import-kalshi-event-metadata `
    --source path\to\your_event_metadata.json `
    --destination-dir reports\kalshi_event_metadata

# 3. Run the dry-run pipeline. The summary will state explicitly whether
#    paper simulation was invoked or skipped, and if skipped, why.
python scan.py run-structural-basket-dry-run `
    --snapshot reports\kalshi_orderbook_enriched_snapshot.json `
    --metadata reports\kalshi_event_metadata\your_event_metadata.json `
    --summary-json-output reports\structural_basket_dry_run_summary.json `
    --summary-markdown-output reports\structural_basket_dry_run_summary.md `
    --enriched-snapshot-output reports\structural_basket_dry_run_enriched_snapshot.json `
    --paper-fill-json-output reports\structural_basket_dry_run_paper_fill_journal.json `
    --paper-fill-markdown-output reports\structural_basket_dry_run_paper_fill_journal.md
```

## Summary fields

The summary JSON includes:

- `metadata_events`, `trusted_metadata_events`, `blocked_metadata_events`,
  `reference_only_metadata_events`
- `matched_events`, `trusted_after_join_events`, `blocked_after_join_events`
- `enriched_normalized_market_rows`
- `structural_groups_evaluated`, `structural_review_count`,
  `stop_for_review_count`, `structural_status_counts`
- `paper_fill_rows`, `paper_fill_simulated_count`, `paper_fill_blocked_count`
- `paper_simulation_skipped`, `paper_simulation_skip_reason`
- `top_blockers` — aggregated across all four stages
- `paper_candidate_count` — always `0` from this pipeline

The summary `safety` block reasserts on every run:

- `saved_file_only=true`
- `live_fetch_attempted=false`
- `places_orders=false`
- `auth_used=false`
- `private_endpoints_used=false`
- `secrets_read=false`
- `browser_automation_used=false`
- `wallet_used=false`
- `paper_candidate_emitted=false`
- `stop_for_review_means_review_only=true`
- `uses_midpoint=false`
- `uses_title_similarity_for_exhaustiveness=false`
- `uses_graph_hints_for_exhaustiveness=false`
- `uses_count_only_evidence=false`

## Failure modes (which are the desired behaviors)

- **No metadata files supplied** → join finds nothing trusted → detector
  evaluates zero groups → simulator is skipped with
  `paper_simulation_skip_reason="no_stop_for_review_row"`.
- **Metadata is reference-only / title-only / per-market Yes/No only /
  count-only / mixed rules-or-times** → audit reports blockers → join
  refuses to emit `normalized_markets` → detector evaluates zero
  groups → simulator skipped.
- **Snapshot is missing one or more metadata tickers** → join records
  `manifest_market_tickers_absent_from_snapshot` → enriched
  `normalized_markets` is empty → simulator skipped.
- **Stale orderbook / insufficient depth / fees kill the gap** → detector
  bins the row as `STALE_ORDERBOOK` / `INSUFFICIENT_DEPTH` / `FEES_KILL` →
  simulator skipped.
- **`--skip-paper-fill-simulation` passed** → simulator skipped with
  `paper_simulation_skip_reason="paper_simulation_disabled_by_caller"` even
  when the detector surfaced `STOP_FOR_REVIEW` rows.

In every failure mode the summary records what blocked the pipeline, the
counts of trusted/matched events, and the top blockers so the next step is
obvious from disk alone.
