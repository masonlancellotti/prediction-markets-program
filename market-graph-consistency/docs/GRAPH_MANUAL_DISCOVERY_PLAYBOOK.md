# Graph Manual Discovery Playbook

Saved-file-only, diagnostic-only playbook for manual relationship-evidence
discovery in the market graph. The graph is the relationship memory and
manual-discovery router. The relative-value-scanner is the strict
exact / paper gate. The two never short-circuit each other.

## When to use this playbook

You're using this playbook when:

- A non-exact relationship between two prediction markets needs to be
  documented (touch vs PIT, midpoint vs upper-bound, sportsbook anchor).
- An RV review is blocked on missing settlement-source / time / fee
  evidence and you need to capture it from the venue rules page.
- A `NO_CURRENT_PEER` row needs a manual peer query against Polymarket /
  CDNA / Kalshi.

If the relationship is **already** exact same-payoff in RV, this playbook
does not apply. The graph never adds or removes RV strict-gate evidence.

## Verticals

The backlog is sliced into three primary verticals:

- **crypto** — payoff calendar (touch vs deadline vs PIT vs daily close)
  and settlement source (CF Benchmarks BRTI/ERTI vs Binance vs oracle).
- **economics** — Fed/FOMC meetings (midpoint vs upper-bound vs effective
  rate), macro releases (revision rules, indicator source/time).
- **sports** — championship boards (MLB / NBA / NHL) with same-field
  winner taxonomy plus reference-only sportsbook anchors.

Each task in the backlog is keyed by `(vertical, family, primary_blocker)`,
so one task covers all rows that the same manual evidence unblocks.

## Backlog field semantics

| Field | What it means |
| --- | --- |
| `task_id` | Stable id of the task — safe to reuse across runs. |
| `vertical` | crypto / economics / sports / structural. |
| `family` | Relationship sub-family (payoff_calendar, rate_definition, event_winner, …). |
| `manual_action` | One-sentence description of the manual step. |
| `source_page` | The venue rules or official source to open. |
| `evidence_to_capture` | The specific text / screenshot / URL to capture. |
| `blocker_cleared` | The deterministic blockers this evidence would clear. |
| `relationships_unlocked` | Stable relationship IDs this task unblocks. |
| `relationships_unlocked_count` | Count of those IDs (used for ranking). |
| `reusable_scope` | How often this work decays (`one_time`, `per_family`, `per_market`, `per_event_date`, `per_venue_rules_version`). |
| `expected_payoff` | One of: `enables_graph_relationship`, `enables_rv_source_review`, `enables_exact_review_candidate`, `only_improves_basis_risk_map`. |
| `urgency` | HIGH / MEDIUM / LOW. HIGH means the row is RV-ready once the evidence is captured. |
| `difficulty` | EASY / MEDIUM / HARD. |
| `fake_edge_risk_if_skipped` | What goes wrong in the graph if you skip this task. |

## Sections

The backlog renders seven sections:

1. **Top 10 overall** — the strongest tasks regardless of vertical.
2. **Top crypto** — payoff-calendar / settlement-source work.
3. **Top economics** — FOMC rate-definition / macro release work.
4. **Top sports** — championship board evidence.
5. **Unblocks most graph edges** — sort by `relationships_unlocked_count`.
6. **Unblocks relative-value review** — only tasks that flip a row from
   `blocked_on_manual_evidence` to `can_go_to_relative_value_now`.
7. **Ignore for now** — `LOW` urgency tasks that only improve the
   basis-risk map and don't enable any gate change.

## Safety rules

- Manual evidence capture is **review-only**. It never authorises
  trading.
- The backlog never claims exact-payoff equality. RV's strict gate is
  the only path that proves exact same-payoff.
- Reference-only sources (sportsbook anchors, oracle feeds, dot-plot
  PDFs) are never treated as executable counterparts.
- Title similarity is never settlement equivalence.
- LLM review of the manual evidence inventory is **advisory only**;
  the validator rejects PAPER_CANDIDATE, executable, exact, or
  blocker-dropping suggestions.

## Companion commands

```
python scan.py graph-manual-relationship-evidence `
  --rv-reports-dir "..\relative-value-scanner\reports" `
  --edges reports/rv_diagnostic_relationship_edges.json `
  --json-output reports/graph_manual_relationship_evidence.json `
  --markdown-output reports/graph_manual_relationship_evidence.md

python scan.py graph-manual-discovery-backlog `
  --relationships reports/graph_manual_relationship_evidence.json `
  --json-output reports/graph_manual_discovery_backlog.json `
  --markdown-output reports/graph_manual_discovery_backlog.md

python scan.py llm-review-graph-manual-evidence `
  --input reports/graph_manual_relationship_evidence.json `
  --sample-size 50 `
  --prompt-output reports/llm_graph_manual_evidence_prompt.md `
  --expected-json-schema-output reports/llm_graph_manual_evidence_schema.json

python scan.py validate-llm-graph-manual-evidence-review `
  --input reports/llm_graph_manual_evidence_output.json `
  --schema reports/llm_graph_manual_evidence_schema.json `
  --relationships reports/graph_manual_relationship_evidence.json `
  --json-output reports/llm_graph_manual_evidence_validation.json
```
