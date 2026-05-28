# Kalshi Local Manifest Template Triage

Date: 2026-05-26

This memo reviews generated `local_manifest_v1` template artifacts and related saved Kalshi metadata/scout reports. It is diagnostic only. No new manifest was written, no manifest was approved, and no validator/evaluator gates were changed.

## Scope Inspected

- Manifest templates: `reports/manifest_templates/`
  - 56 template files present.
  - All inspected templates are intentionally invalid by default: `trusted_local_manifest=false`, `complete=false`, empty `outcome_list`, and `TODO` evidence placeholders.
- Structural hunt report: `reports/structural_basket_hunt.json`
  - `manifest_template_suggestions`: 75
  - `manifest_templates_written`: 56
  - `manifest_templates_still_invalid`: 75
  - `ready_stop_for_review_count`: 0
- Kalshi event metadata audit: `reports/e2e_demo/audit.json`
  - One trusted event exists, `KXE2E-2026-DEMO`, but it is an end-to-end demo fixture, not a production manifest template candidate.
- Related scout/audit reports:
  - `reports/manifest_scouts/*.json`
  - `reports/native_group_audits/*.json`
  - `reports/structural_basket_dry_run_structural.json`

## Strict Finding

No production manifest template currently has saved Kalshi event-level metadata sufficient to pass strict `local_manifest_v1` validation.

The blocker is not the template format. The blocker is evidence. The saved production candidates generally have per-market rules, apparent outcome labels, and sometimes orderbook enrichment, but they do not have the required event-level explicit outcome list, explicit completeness/exhaustiveness marker, settlement source evidence, and reviewer-completed manifest fields.

Per-market Yes/No outcomes, ticker suffixes, title patterns, and market counts were not treated as exhaustive evidence.

## Validation Bar

`local_manifest_v1` validation requires, at minimum:

- `source=local_manifest_v1`
- `trusted_local_manifest=true`
- `reviewer`
- ISO-like `reviewed_at`
- `venue`
- group/event id
- exact market tickers
- event-level complete `outcome_list`
- `complete=true` or equivalent exhaustive marker
- `evidence_text`
- `settlement_source_evidence`
- `rules_evidence`
- no title-only evidence
- no graph-hint evidence
- no reference-only source

## Top 5 Review Targets

These are the five best review targets I found, ranked by practical review shape and saved diagnostic surface. None is ready to pass without human-added evidence.

### 1. KXMLB-26 - 2026 Pro Baseball Championship

- Venue: Kalshi
- Event id / ticker: `KXMLB-26`
- Representative template: `reports/manifest_templates/mlb_kxmlb_kalshi_snapshot__kxmlb-26.template.json`
- Related scout: `reports/manifest_scouts/reports_mlb_kxmlb_kalshi_snapshot.json`
- Market tickers: `KXMLB-26-WSH`, `KXMLB-26-TOR`, `KXMLB-26-TEX`, `KXMLB-26-TB`, `KXMLB-26-STL`, `KXMLB-26-SF`, `KXMLB-26-SEA`, `KXMLB-26-SD`, `KXMLB-26-PIT`, `KXMLB-26-PHI`, `KXMLB-26-NYY`, `KXMLB-26-NYM`, `KXMLB-26-MIN`, `KXMLB-26-MIL`, `KXMLB-26-MIA`, `KXMLB-26-LAD`, `KXMLB-26-LAA`, `KXMLB-26-KC`, `KXMLB-26-HOU`, `KXMLB-26-DET`, `KXMLB-26-CWS`, `KXMLB-26-COL`, `KXMLB-26-CLE`, `KXMLB-26-CIN`, `KXMLB-26-CHC`, `KXMLB-26-BOS`, `KXMLB-26-BAL`, `KXMLB-26-AZ`, `KXMLB-26-ATL`, `KXMLB-26-ATH`
- Apparent per-market outcomes, not strict event-level evidence: Washington, Toronto, Texas, Tampa Bay, St. Louis, San Francisco, Seattle, San Diego, Pittsburgh, Philadelphia, New York Y, New York M, Minnesota, Milwaukee, Miami, Los Angeles D, Los Angeles A, Kansas City, Houston, Detroit, Chicago WS, Colorado, Cleveland, Cincinnati, Chicago C, Boston, Baltimore, Arizona, Atlanta, A's
- Explicit outcome list present: no
- Explicit completeness evidence present: no
- Settlement source URL present: no
- Resolution rules/source evidence present: per-market rules such as `If Washington wins the 2026 Pro Baseball Championship, then the market resolves to Yes.` No event-level shared source evidence was found.
- Shared settlement evidence present: no
- Orderbook/depth/freshness evidence present: yes, in scout/enriched snapshots; scout reports `has_orderbooks=true`, min depth about `1.56`, provisional sum asks about `1.023`, but status is `BLOCKED_STALE` with stale quotes.
- Exact missing pieces for reviewer:
  - official/saved Kalshi event page or event metadata showing the complete list of all outcomes
  - explicit source text proving every listed market shares the same event result
  - settlement source evidence, ideally source URL or saved source text
  - rules evidence that is event-level, not just per-market title/rule pattern
  - completed `reviewer`, `reviewed_at`, `trusted_local_manifest=true`, `complete=true`, and full `outcome_list`
  - fresh orderbook/depth evidence before any paper-review simulation
- Likely to pass `local_manifest_v1`: not with current saved evidence. Best production review target if a human supplies official event-level completeness/source evidence.

### 2. KXNBA-26 - 2026 Pro Basketball Finals

- Venue: Kalshi
- Event id / ticker: `KXNBA-26`
- Representative templates:
  - `reports/manifest_templates/kalshi_orderbook_enriched_snapshot__kxnba-26.template.json`
  - `reports/manifest_templates/nba_kxnba_kalshi_snapshot__kxnba-26.template.json`
- Related scouts:
  - `reports/manifest_scouts/reports_kalshi_orderbook_enriched_snapshot.json`
  - `reports/manifest_scouts/reports_nba_kxnba_kalshi_snapshot.json`
- Market tickers: `KXNBA-26-SAS`, `KXNBA-26-OKC`, `KXNBA-26-NYK`, `KXNBA-26-CLE`
- Apparent per-market outcomes, not strict event-level evidence: San Antonio, Oklahoma City, New York, Cleveland
- Explicit outcome list present: no
- Explicit completeness evidence present: no
- Settlement source URL present: no
- Resolution rules/source evidence present: per-market rules such as `If San Antonio win the 2026 Pro Basketball Finals, then the market resolves to Yes.`
- Shared settlement evidence present: no
- Orderbook/depth/freshness evidence present: yes; scout reports `has_orderbooks=true`, min depth about `255319.56` in `reports_kalshi_orderbook_enriched_snapshot`, provisional sum asks about `1.04`, but status is `BLOCKED_STALE`.
- Exact missing pieces for reviewer:
  - explicit Kalshi event-level outcome list proving these four are the complete active outcome set, if that is true
  - explicit completeness marker/evidence from saved event metadata or a reviewed manifest
  - settlement source and rules evidence covering all outcomes
  - completed reviewer fields and trust/completeness flags
  - refreshed quotes/orderbook depth
- Likely to pass `local_manifest_v1`: not with current saved evidence. The small four-outcome set is reviewable, but also risky because the saved files do not prove those four outcomes are exhaustive.

### 3. KXNHL-26 - 2025-26 Stanley Cup Finals

- Venue: Kalshi
- Event id / ticker: `KXNHL-26`
- Representative template: `reports/manifest_templates/nhl_kxnhl_kalshi_snapshot__kxnhl-26.template.json`
- Related scout: `reports/manifest_scouts/reports_nhl_kxnhl_kalshi_snapshot.json`
- Market tickers: `KXNHL-26-VGK`, `KXNHL-26-MTL`, `KXNHL-26-COL`, `KXNHL-26-CAR`
- Apparent per-market outcomes, not strict event-level evidence: Vegas Golden Knights, Montreal Canadiens, Colorado Avalanche, Carolina Hurricanes
- Explicit outcome list present: no
- Explicit completeness evidence present: no
- Settlement source URL present: no
- Resolution rules/source evidence present: per-market rules such as `If the Vegas Golden Knights win the 2025-26 Stanley Cup Finals, then the market resolves to Yes.`
- Shared settlement evidence present: no
- Orderbook/depth/freshness evidence present: yes; scout reports `has_orderbooks=true`, min depth about `10490.2`, provisional sum asks about `1.03`, but status is `BLOCKED_STALE`.
- Exact missing pieces for reviewer:
  - official/saved Kalshi event metadata showing the complete outcome set
  - event-level source and rules evidence
  - completed manifest reviewer/trust/completeness fields
  - fresh quotes/orderbook depth
- Likely to pass `local_manifest_v1`: not with current saved evidence. It is reviewable only if the four-team list is explicitly backed by event-level completeness evidence.

### 4. KXBULGARIAPRES-26NOV15 - 2026 Bulgarian Presidential Election

- Venue: Kalshi
- Event id / ticker: `KXBULGARIAPRES-26NOV15`
- Representative template: `reports/manifest_templates/kalshi_live_readonly_snapshot__kxbulgariapres-26nov15.template.json`
- Related scout: `reports/manifest_scouts/overlap_politics_election_kalshi_live_readonly_snapshot.json`
- Market tickers: `KXBULGARIAPRES-26NOV15-VTER`, `KXBULGARIAPRES-26NOV15-YSTO`, `KXBULGARIAPRES-26NOV15-RZHE`, `KXBULGARIAPRES-26NOV15-NKIS`, `KXBULGARIAPRES-26NOV15-NDEN`, `KXBULGARIAPRES-26NOV15-KZAR`, `KXBULGARIAPRES-26NOV15-KKOS`, `KXBULGARIAPRES-26NOV15-IYOT`, `KXBULGARIAPRES-26NOV15-BKOT`, `KXBULGARIAPRES-26NOV15-AATA`
- Apparent per-market outcomes, not strict event-level evidence: Vasil Terziev, Yanaki Stoilov, Rosen Zhelyazkov, Nataliya Kiselova, Nikolai Denkov, Krum Zarkov, Kostadin Kostadinov, Iliana Yotova, Blagomir Kotsev, Atanas Atanasov
- Explicit outcome list present: no
- Explicit completeness evidence present: no
- Settlement source URL present: no
- Resolution rules/source evidence present: yes at per-market level, with substantial election-resolution text in `rules_secondary`; not enough to prove the candidate list is exhaustive.
- Shared settlement evidence present: no explicit shared source URL or reviewed event-level source.
- Orderbook/depth/freshness evidence present: yes; scout reports `has_orderbooks=true`, min depth about `1.0`, provisional sum asks about `1.305`, but status is `BLOCKED_STALE`.
- Exact missing pieces for reviewer:
  - official/saved Kalshi event page or event metadata proving the listed candidate markets are exhaustive
  - event-level settlement source evidence
  - event-level rules evidence or proof the per-market rules are identical except candidate name
  - completed manifest reviewer/trust/completeness fields
  - refreshed quotes/orderbook depth
- Likely to pass `local_manifest_v1`: not with current saved evidence. Lower confidence than sports finals because candidate lists can be open-ended or change, and no saved completeness marker was found.

### 5. KXFOMCDISSENTCOUNT-26JUN - FOMC Dissent Count

- Venue: Kalshi
- Event id / ticker: `KXFOMCDISSENTCOUNT-26JUN`
- Representative template: `reports/manifest_templates/kalshi_live_readonly_snapshot__kxfomcdissentcount-26jun.template.json`
- Related scout: `reports/manifest_scouts/fed_kalshi_live_readonly_snapshot.json`
- Market tickers: `KXFOMCDISSENTCOUNT-26JUN-4`, `KXFOMCDISSENTCOUNT-26JUN-3`, `KXFOMCDISSENTCOUNT-26JUN-2`, `KXFOMCDISSENTCOUNT-26JUN-1`, `KXFOMCDISSENTCOUNT-26JUN-0`
- Apparent per-market outcomes, not strict event-level evidence: 4, 3, 2, 1, 0
- Explicit outcome list present: no
- Explicit completeness evidence present: no
- Settlement source URL present: no
- Resolution rules/source evidence present: per-market rules such as `If there are exactly 4 dissenting votes at the next scheduled FOMC meeting (scheduled for June 17, 2026), then the market resolves to Yes.`
- Shared settlement evidence present: no
- Orderbook/depth/freshness evidence present: yes; scout reports `has_orderbooks=true`, min depth about `20.0`, provisional sum asks about `1.14`, but status is `BLOCKED_STALE`.
- Exact missing pieces for reviewer:
  - saved event-level metadata proving the count buckets are complete and no `5+` or other bucket is missing
  - explicit settlement source evidence for dissent count
  - event-level rules evidence that all buckets share the same resolution source and timing
  - completed manifest reviewer/trust/completeness fields
  - refreshed quotes/orderbook depth
- Likely to pass `local_manifest_v1`: not with current saved evidence. The `0` through `4` shape is promising only if Kalshi explicitly says those are all possible/available outcomes for this event.

## Candidates I Did Not Rank Higher

- `KXBTC-*`, `KXBTCD-*`, `KXETH-*`: large template groups exist, but these are threshold ladders, not an explicitly proven mutually exclusive exhaustive outcome board in the saved files. They must not be promoted by count, title, or ticker shape.
- `KXFED-*` rate-threshold groups: saved rules/source text is useful for family review, but the visible markets are monotonic "above" thresholds, not an explicitly proven exact exhaustive bucket board.
- Single-market templates such as `KXACQANNOUNCESPACEX-27JAN01` and `KXBRAZILPRES1R-26OCT04`: they may have good per-market rules, but they are not useful same-venue structural basket manifests without an event-level outcome board.

## Control Evidence Found

`reports/e2e_demo/audit.json` contains a fully trusted Kalshi event-metadata fixture for `KXE2E-2026-DEMO`:

- explicit event-level `outcome_list`
- `complete=true`
- `is_exhaustive=true`
- `all_outcomes_included=true`
- rules evidence
- settlement source raw evidence
- matching market tickers

That proves the local pipeline can recognize strict event metadata when it exists. It does not prove any production template is ready.

## Human Review Readiness

No candidate is ready to pass `local_manifest_v1` based only on saved evidence.

The best candidate for human manifest review is `KXMLB-26`, because it has the broadest saved production board, apparent per-market outcome labels, and saved orderbook/depth data. A reviewer still must supply explicit event-level completeness, outcome-list, rules, and settlement-source evidence before any manifest can be trusted.

No paper simulation or paper review should run from these templates until a completed manifest validates cleanly and fresh orderbook/depth/freshness gates pass.

## Safety Confirmation

- No manifest was written.
- No manifest was approved.
- No validator/evaluator gates were changed.
- No exhaustiveness was inferred from market count, title, ticker, or graph hints.
- No live API calls were made.
- No orders, auth, account, balance, position, wallet, signing, or private-key logic was added.
- No `PAPER_CANDIDATE` was emitted.
