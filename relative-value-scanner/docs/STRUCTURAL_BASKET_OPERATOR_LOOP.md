# Structural Basket Operator Loop

Saved-file-only operator checklist for getting from saved Kalshi market snapshots
to one credible STOP_FOR_REVIEW structural basket, and from there to an honest
saved-file paper-fill journal. No live trading. No live API calls. No order
placement. No `PAPER_CANDIDATE` is ever promoted. `STOP_FOR_REVIEW` is
review/report-only.

## Hard rules

- Saved-file only. Every step reads JSON from disk.
- Reference-only sources (`reference_only` / `source_kind=reference` /
  `venue_type=reference_only`) fail closed.
- Per-market Yes/No is NOT an event-level `outcome_list`.
- No exhaustiveness is inferred from title, ticker, market count, or graph hints.
- Manifest templates the hunter writes are **invalid by default**; they cannot
  feed `STOP_FOR_REVIEW` until a reviewer fills every placeholder AND validation
  passes.
- The paper-fill simulator is only invoked on rows the structural detector
  already promoted to `STOP_FOR_REVIEW`.

## The loop

### 1. Run the hunter

```bash
python scan.py hunt-structural-basket-candidates \
  --snapshots-dir reports \
  --metadata-dir reports \
  --manifest-dir reports/manifest_templates \
  --json-output reports/structural_basket_hunt.json \
  --markdown-output reports/structural_basket_hunt.md \
  --manifest-template-dir reports/manifest_templates
```

Useful variants:

```bash
# Larger top-N + skip paper-fill simulation (diagnostics only)
python scan.py hunt-structural-basket-candidates --top-closest-n 20 --skip-paper-fill-simulation
```

The hunter never writes `PAPER_CANDIDATE`, never calls a live API, and never
modifies the structural detector's economic or trust gates.

### 2. Inspect the top profit-readiness ladder row

Open `reports/structural_basket_hunt.md`. Read the **Profit-readiness ladder**
section. It is ordered from "go now" to "dead end":

1. `READY_STOP_FOR_REVIEW` — paper-simulate now (saved-file only).
2. `NEEDS_VALID_MANIFEST` — a hunter-written template is staged; complete it.
3. `NEEDS_EVENT_METADATA` — acquire and import the saved Kalshi event metadata.
4. `NEEDS_FRESH_QUOTES` — refresh the saved snapshot externally.
5. `NEEDS_DEPTH` — wait for deeper top-of-book.
6. `FEES_KILL` — wait for a wider basket gap.
7. `REFERENCE_ONLY_BLOCKED` — skip; reference-only sources are not executable
   legs.
8. `NOT_EXHAUSTIVE_EVIDENCE` — inspect per-pairing detail.

The **Closest groups to review** table shows the top-N entries with their
`profit_readiness`, `why_not_stop_for_review`, `best_next_action`, and
manifest-template state.

### 3. Complete the manifest manually with explicit evidence

If the top row is `NEEDS_VALID_MANIFEST`, open the template path printed in the
row (under `reports/manifest_templates/<safe_name>.template.json`). It is
intentionally invalid — every editable field is `null` and
`trusted_local_manifest` is `false`. Manually fill:

- `reviewer` — your handle or email
- `reviewed_at` — ISO-8601 timestamp
- `evidence_text` — citation of the saved Kalshi event page evidence
- `settlement_source_evidence` — verbatim settlement source
- `rules_evidence` — verbatim rules / resolution text
- `outcome_list` — the explicit event-level outcomes
- `complete: true` and `trusted_local_manifest: true`

Run validation (the next hunt run will re-validate automatically). The hunter
marks `manifest_template_still_invalid: false` only when
`validate_local_manifest_v1_group` passes. Until then, the template cannot
promote any row.

### 4. Re-run the dry-run

After the manifest is valid, re-run the hunter (or the focused dry-run command):

```bash
python scan.py run-structural-basket-dry-run \
  --snapshot reports/<your_kalshi_snapshot>.json \
  --metadata reports/<your_event_metadata>.json \
  --structural-json-output reports/<safe>_structural.json \
  --structural-markdown-output reports/<safe>_structural.md
```

Confirm at least one row in `reports/<safe>_structural.md` is
`STOP_FOR_REVIEW`. If none appears, walk the **Shortest blocker chain to first
STOP_FOR_REVIEW** in the hunter report — it lists the 1–3 step path for the
highest-rank non-ready entry.

### 5. Paper-simulate only on STOP_FOR_REVIEW rows

```bash
python scan.py simulate-paper-fills \
  --input reports/<safe>_structural.json \
  --json-output reports/<safe>_paper_fill_journal.json \
  --markdown-output reports/<safe>_paper_fill_journal.md
```

The simulator independently rejects ungated rows even if the orchestration
layer is bypassed. STOP_FOR_REVIEW is the only status that produces a
simulated fill; everything else is `blocked` and recorded with explicit
blockers.

### 6. Never trade from these reports

STOP_FOR_REVIEW is **review-only**. The paper-fill journal is **saved-file
only**. Nothing in this loop authorizes order placement, authentication, or
any live action. Live trading remains explicitly out of scope.

## Safety surface

Every report carries an explicit `safety` block asserting:

- `saved_file_only: true`
- `live_fetch_attempted: false`
- `places_orders: false`
- `auth_used: false`
- `private_endpoints_used: false`
- `secrets_read: false`
- `browser_automation_used: false`
- `wallet_used: false`
- `paper_candidate_emitted: false`
- `stop_for_review_means_review_only: true`
- `uses_midpoint: false`
- `uses_title_similarity_for_exhaustiveness: false`
- `uses_graph_hints_for_exhaustiveness: false`
- `uses_count_only_evidence: false`
- `infers_exhaustiveness_from_title: false`
- `infers_exhaustiveness_from_ticker: false`
- `infers_exhaustiveness_from_market_count: false`
- `templates_are_valid_by_default: false`
- `allowed_actions: ["WATCH", "MANUAL_REVIEW", "MANIFEST_REVIEW"]`

Static and runtime tests verify these invariants on every release.
