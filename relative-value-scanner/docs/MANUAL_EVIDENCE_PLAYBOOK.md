# Manual Evidence Playbook

**Purpose.** Concentrate every manual evidence collection step Mason needs to
move Crypto / Economics / Sports rows from `missing_evidence` into
`source_review` or `manual_review`. The machine-readable equivalent of this
document is regenerated at
`reports/manual_evidence_requirements.json` /
`reports/manual_evidence_requirements.md`
by `python scan.py manual-evidence-requirements`.

**Safety contract.** None of the items below clear evaluator gates, lower the
exact-payoff bar, or create paper candidates by themselves. Manual evidence
only moves rows along the source-review track until the existing strict
relative-value gates pass.

- No trading.
- No order placement / cancellation / private endpoints.
- No credentials stored.
- Queued platforms (IBKR ForecastEx, SX Bet, ProphetX) stay queued.
- Reference-only platforms (The Odds API, official feeds) stay reference.
- Manual evidence on its own → can only move rows to source-review or
  manual-review.

---

## How to use this playbook

1. Run the saved-file-only generator first:

   ```
   python scan.py manual-evidence-requirements `
     --input-dir reports `
     --json-output reports/manual_evidence_requirements.json `
     --markdown-output reports/manual_evidence_requirements.md
   ```

2. Re-run the ops-status command so the new block appears at the top of the
   dashboard:

   ```
   python scan.py relative-value-ops-status `
     --input-dir reports `
     --json-output reports/relative_value_ops_status.json `
     --markdown-output reports/relative_value_ops_status.md
   ```

3. Pick a task from the Top-10 lists below. Every catalogue item carries the
   exact rules-text URL pattern, the screenshot to capture, the local file to
   save into, and the existing safe repo command to validate with.

4. After capturing evidence, re-run the validation command listed in the
   item. The catalogue item moves from `missing` to `captured_unreviewed`,
   then becomes `validated_diagnostic` after the relevant saved-file diagnostic
   re-classifies the row.

---

## Priority legend

- **P0** — blocks all progress until captured (e.g. fresh Kalshi orderbook).
- **P1** — needed for the source-review tier of the affected family.
- **P2** — needed for exact-review tier of the affected family.
- **P3** — needed for the eventual paper-review tier.
- **P4** — nice-to-have, or a guard item to prevent accidental promotion.

## Status legend

- `missing` — no value captured yet.
- `partially_captured` — some fields captured, full evidence still missing.
- `captured_unreviewed` — full value captured but not human-verified.
- `validated_diagnostic` — passed the saved-file diagnostic; still not
  evaluator-ready on its own.
- `reviewed_usable` — explicitly reviewed and signed off for review tier
  consumption (still does not auto-clear evaluator/exact gates).
- `blocked` — value is permanently unobtainable from this platform/family.

## Verticals + platform status

- **Crypto** — Kalshi (active), Polymarket (active), CDNA (active), IBKR
  (queued), SX Bet (queued), ProphetX (queued).
- **Economics** — Kalshi (active), Polymarket (active), CDNA (active for
  ETH/BTC priors), IBKR (queued; FOMC contracts visible in saved diagnostics
  but not active).
- **Sports** — Kalshi (active), Polymarket (active), The Odds API
  (**reference only** — never sits on either side of an exact pair),
  SX Bet (queued), ProphetX (queued).

---

## Do-Once Checklist (one_time_per_family)

These items are collected once per family and reused indefinitely until the
underlying rules version changes.

- [ ] `cry-kal-range-bucket-inclusive` — Kalshi crypto bucket inclusivity
      (left-closed / right-open / inclusive both sides). Mis-recording this
      causes a fake-edge.
- [ ] `cry-kal-weekly-friday-rules` — Confirm Kalshi weekly Friday close
      shares ticker family + BRTI methodology with daily 5pm.
- [ ] `cry-poly-point-in-time-existence` — Kill-or-confirm investigation:
      does any Polymarket BTC "above X at 5pm ET on weekday" market exist?
- [ ] `eco-kal-fomc-meeting-date` — Save the FOMC calendar JSON anchor.
- [ ] `eco-official-fomc-statement` — Save Federal Reserve press-release +
      NY Fed effective-rate URLs into `docs/SOURCE_TAXONOMY.md`.
- [ ] `eco-macro-kalshi-coverage` — Confirm whether Kalshi CPI/payrolls/GDP
      markets are even present in saved snapshots.
- [ ] `eco-ibkr-fomc-queued` — Re-confirm IBKR ForecastEx remains queued
      (read-only memo) and do not promote it.
- [ ] `spt-odds-fv-reference-only` — Confirm Odds API never becomes a pair
      side.
- [ ] `spt-kal-nfl-super-bowl-existence` — Confirm whether the Kalshi NFL
      Super Bowl market family exists in saved snapshots at all.
- [ ] `x-source-taxonomy-anchor-urls` — Per-family anchor URLs in
      `docs/SOURCE_TAXONOMY.md`.

## Do-Per-Market-Family Checklist (per_rules_version / per_fee_schedule_change)

- [ ] `cry-kal-hourly-rules-text` — Kalshi BTC/ETH hourly rules text →
      confirm strict vs inclusive comparator + 60-second BRTI/ERTI window.
- [ ] `cry-kal-5pm-daily-rules` — Save CF Benchmarks BRTI methodology PDF.
- [ ] `cry-cdna-ath-rules` — CDNA ATH event rules text per rules version.
- [ ] `cry-cdna-year-end-range-bucket` — CDNA year-end range-bucket rules
      per rules version.
- [ ] `cry-cdna-earliest-touch-rules` — CDNA earliest-touch rules.
- [ ] `cry-kal-fee-schedule` — Kalshi crypto category fee schedule.
- [ ] `eco-kal-fomc-fee-schedule` — Kalshi FOMC category fee schedule.
- [ ] `spt-fee-evidence-per-family` — Kalshi sports per-category fee
      schedule.
- [ ] `x-fee-evidence-registry` — Cross-family fee memo for evaluator
      consumption.

## Do-Per-Specific-Market Checklist (per_market / per_event / per_contract_month)

- [ ] `cry-poly-hit-touch-rules-text` — full rules text per Polymarket
      hit/touch market.
- [ ] `cry-poly-up-down-rules` — full rules text per Polymarket up/down
      market.
- [ ] `cry-poly-clob-token-ids` — clobTokenIds per Polymarket crypto market.
- [ ] `cry-cdna-eth-pit-fixture` — CDNA ETH PIT event-page snapshot.
- [ ] `cry-cdna-btc-pit-fixture` — CDNA BTC PIT event-page snapshot (on a
      Kalshi-aligned date).
- [ ] `eco-kal-fomc-rate-bound-definition` — `rate_bound` per KXFED ticker.
- [ ] `eco-kal-fomc-comparator-strictness` — comparator + threshold_percent
      per KXFED ticker.
- [ ] `eco-poly-fomc-existence` — Polymarket FOMC market discovery target.
- [ ] `eco-poly-fomc-bound-alignment` — Polymarket FOMC bound alignment.
- [ ] `spt-kal-nba-championship-rules` — Kalshi NBA championship rules per
      ticker.
- [ ] `spt-kal-mlb-world-series-rules` — Kalshi MLB rules + settlement_date
      per ticker.
- [ ] `spt-kal-nhl-stanley-cup-rules` — Kalshi NHL rules per ticker.
- [ ] `spt-poly-nba-token-ids` — Polymarket NBA rules + token IDs.
- [ ] `spt-poly-mlb-token-ids` — Polymarket MLB rules + settlement clause.
- [ ] `spt-poly-nhl-token-ids` — Polymarket NHL rules + settlement clause.
- [ ] `spt-poly-nfl-token-ids` — Polymarket NFL rules sample (until KXNFL
      exists).
- [ ] `spt-same-payoff-board-review` — per-pair review notes.

## Do-Before-Any-Paper-Review Checklist (can_ever_affect_paper_review = true)

These items must be captured before any single row crosses the paper-review
threshold. Capturing them does NOT clear the gates — it only makes the gates
*evaluable*.

- [ ] `cry-kal-fresh-orderbook` — Fresh Kalshi orderbook depth snapshot.
- [ ] `cry-kal-fee-schedule` — Kalshi crypto category fees.
- [ ] `cry-poly-clob-token-ids` — Polymarket crypto clobTokenIds.
- [ ] `eco-kal-fomc-fresh-quote` — Fresh Kalshi FOMC orderbook depth.
- [ ] `eco-kal-fomc-fee-schedule` — Kalshi FOMC fees.
- [ ] `spt-fee-evidence-per-family` — Kalshi per-sport fees.
- [ ] `x-fee-evidence-registry` — Cross-family fee memo.
- [ ] `spt-poly-mlb-token-ids` — Polymarket MLB settlement clause.
- [ ] `spt-poly-nhl-token-ids` — Polymarket NHL settlement clause.
- [ ] `spt-same-payoff-board-review` — Per-pair same-payoff review notes.

---

## Vertical-by-vertical assessment

### Crypto

- **Closest to exact-review?** No. Saved data shows
  `exact_shape_possible_rows=58`, all of which are Polymarket↔CDNA touch
  pairings — *zero* involve Kalshi. The structural reason: Kalshi crypto is
  weekly/hourly close, Polymarket crypto is touch/up-down/year-end anchored.
- **Highest-leverage manual action**:
  `cry-poly-point-in-time-existence` (P0, one-time per family). This
  investigation kills-or-confirms the entire Kalshi↔Polymarket crypto lane.
  If no Polymarket close-price market exists on a Kalshi-aligned date, the
  lane is closed.
- **Second highest-leverage**:
  `cry-kal-fresh-orderbook` (P0). Without fresh depth, no Kalshi crypto row
  can ever cross paper-review.

### Economics (Fed/FOMC + macro)

- **Closest to source-review?** Yes — already 218/228 Fed/FOMC rows reach
  `family_typed_ready`. The blockers are
  `missing_quote_captured_at` (228 rows) and
  `missing_typed_key:rate_bound` (10 rows).
- **Highest-leverage manual action**:
  `eco-kal-fomc-fresh-quote` (P0, per session). One fresh snapshot moves all
  228 Fed rows past the quote-freshness gate.
- **Second highest-leverage**:
  `eco-kal-fomc-rate-bound-definition` (P0, per market). Captures the bound
  for the remaining 10 typed-incomplete rows.
- **Polymarket-side missing**: A single Polymarket FOMC market on the same
  meeting date with the same bound would create the first real economics
  cross-venue peer.

### Sports

- **Closest to exact-review?** Yes — MLB / NBA / NHL each have a small set
  of Kalshi futures markets already paired against Polymarket peers; the
  blockers are settlement-date alignment + per-pair same-payoff review.
- **Highest-leverage manual action**:
  `spt-poly-mlb-token-ids` (P0, per event). The MLB lane has 30 Kalshi rows
  paired against 780 Polymarket rows but every pair is rejected by
  `settlement_delta_exceeds_limit`. Capturing the Polymarket settlement
  clause is the only path to fixing it.
- **Second highest-leverage**:
  `spt-same-payoff-board-review` (P1, per event). Required for any pair
  to leave WATCH.
- **NFL** is the largest *missing* lane: 476 Polymarket Super Bowl rows
  with zero Kalshi peers. `spt-kal-nfl-super-bowl-existence` is the trigger
  to refresh Kalshi NFL data.

---

## Top 10 manual actions for Mason this week

1. `eco-kal-fomc-fresh-quote` (**P0** — unblocks 228 Fed rows in one
   snapshot).
2. `cry-kal-fresh-orderbook` (**P0** — unblocks ~2516 crypto rows;
   most will still be tagged `closed_or_settled_empty_book` but real
   active rows surface).
3. `cry-poly-point-in-time-existence` (**P0** — kill-or-confirm the entire
   Kalshi↔Polymarket crypto lane).
4. `eco-kal-fomc-rate-bound-definition` (**P0** — typed-key gate for the 10
   blocked Fed rows).
5. `eco-kal-fomc-comparator-strictness` (**P0** — companion to above).
6. `spt-poly-mlb-token-ids` (**P0** — required to clear
   settlement_delta_exceeds_limit for MLB pairs).
7. `cry-poly-hit-touch-rules-text` (**P1** — unblocks 333 Polymarket touch
   rows for source-review classification).
8. `cry-cdna-eth-pit-fixture` (**P1** — CDNA ETH PIT fixture refresh).
9. `cry-poly-clob-token-ids` (**P1** — required for Polymarket CLOB book
   refresh on any future pair).
10. `spt-kal-nfl-super-bowl-existence` (**P1** — open or close the NFL lane).

## Manual actions NOT worth doing yet

- Anything labelled `platform_status=queued` (IBKR ForecastEx, SX Bet,
  ProphetX): do not invest manual effort until the queued platforms are
  explicitly promoted.
- `cry-cdna-earliest-touch-rules` (P4) — CDNA earliest-touch is basis-risk
  versus any close-price market regardless of capture.
- `spt-poly-nfl-token-ids` (P3) — chasing Polymarket NFL rules text is
  wasted unless Kalshi NFL data exists.
- `spt-odds-fv-reference-only` (P4) — guard item; nothing to do.
- `cry-cdna-ath-rules` (P3) — ATH-by-date is permanently basis-risk versus
  threshold close; only matters for CDNA-vs-CDNA ATH comparisons.

---

## Brutally honest bottom line

- **Closest to source-review**: **Economics (Fed/FOMC)**. 218/228 rows are
  already typed-complete; a single fresh quote snapshot + a 10-row manual
  bound capture clears the family.
- **Closest to exact-review**: **Sports (MLB/NBA/NHL)**. Pairings already
  exist; the bottleneck is per-pair settlement-clause + same-payoff-board
  review notes — *not* missing data on Kalshi.
- **Distraction right now**: **Crypto**. The Polymarket crypto catalogue and
  the Kalshi crypto catalogue are structurally non-overlapping (touch /
  up-down / year-end on one side vs weekly/hourly close on the other). The
  one investigation worth doing is the
  `cry-poly-point-in-time-existence` kill-or-confirm. If that returns
  "no such markets exist", crypto cross-venue is dead until one of the
  venues redesigns its product line.
