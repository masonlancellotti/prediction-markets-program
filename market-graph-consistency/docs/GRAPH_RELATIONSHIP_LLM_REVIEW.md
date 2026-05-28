# Graph Relationship LLM Review

Saved-file-only, diagnostic-only contract for using an LLM to **review**
graph relationship edges produced by the RV diagnostic ingestor.

The graph LLM never gates evaluator inputs, never creates paper or
executable claims, and never lowers any blocker that the deterministic
classifier already attached.  It can only *suggest* — and validated
suggestions still require human review before they affect the graph.

## What the LLM is for

The market graph is the **relationship intelligence layer** for non-exact
relationships between prediction markets.  The deterministic classifier
in `rv_diagnostic_ingest.py` assigns a relationship type, action, and
confidence bucket from typed-key evidence in the RV diagnostic reports.
An LLM is allowed to *review* that classification and:

- agree or disagree with the deterministic type;
- propose a different relationship type from the allowed taxonomy;
- propose extra blockers the deterministic classifier missed;
- propose specific manual checks (settlement source URL, time zone,
  fee model, quote freshness window);
- propose a confidence bucket downgrade;
- flag fake-edge risks (title similarity, deadline-touch vs PIT,
  range vs threshold touch, all-time-high-by-date vs PIT, etc.);
- attach reviewer notes for the human reviewer.

## What the LLM is NOT for

| Forbidden                                          | Why                                                                 |
| -------------------------------------------------- | ------------------------------------------------------------------- |
| Claiming `PAPER_CANDIDATE`                         | Graph never creates evaluator inputs.                               |
| Setting `executable=true`                          | Graph never authorises trading.                                     |
| Setting `exact=true`                               | Only RV strict gate proves exact payoff equality.                   |
| Dropping deterministic blockers                    | LLM may *add* blockers; never remove them.                          |
| Treating title similarity as settlement equivalence | Title similarity is a weak signal, never structural evidence.       |
| Treating deadline-touch as point-in-time            | Different payoff shapes; never collapse them.                       |
| Recommending order placement / account ops         | Graph repo has no trading code, period.                             |

## How the workbench works

1. `scan.py ingest-relative-value-diagnostics` reads saved RV reports
   and produces `rv_diagnostic_relationship_edges.json`.
2. `scan.py llm-review-graph-relationships` writes
   `llm_graph_relationship_review_prompt.md` (a bounded prompt) and
   `llm_graph_relationship_review_schema.json` (a strict output schema).
3. The operator pastes the prompt into Claude / GPT and saves the LLM's
   JSON response somewhere local (e.g.
   `reports/llm_graph_relationship_review_output.json`).
4. `scan.py validate-llm-graph-relationship-review` checks the LLM
   response against the schema and the safety contract.  Any row that
   claims paper/executable/exact, drops a deterministic blocker, or
   uses prohibited vocabulary is rejected.
5. Validated rows are stored as **advisory evidence only**.  A separate
   human-reviewed apply command would be required to mutate the graph
   — this module never does that automatically.

## Edge schema (graph-safe alias)

The graph's safety vocabulary forbids the substring `paper_candidate`
and the standalone token `executable`, so the RV-edge taxonomy uses
**`can_emit_evaluator_input=false`** as the graph-safe alias for the
`can_create_paper_candidate=false` concept used in RV reports.  The
semantic is the same: graph never creates evaluator inputs, never
authorises a paper candidate, and never bypasses the RV strict gate.

## Allowed output

The schema in `llm_graph_relationship_review_schema.json` is the
single source of truth.  Operators should keep the schema file fresh
by re-running the prompt generator whenever the relationship taxonomy
changes.
