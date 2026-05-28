# Graph LLM Manual-Evidence Review

Saved-file-only, diagnostic-only contract for using an LLM to review
the graph **manual relationship evidence inventory**.

This sibling workflow to `GRAPH_RELATIONSHIP_LLM_REVIEW.md` operates on
the *manual evidence* layer: each row in
`graph_manual_relationship_evidence.json` is a non-exact relationship
with a list of `manual_evidence_needed`, and the LLM may suggest
extensions to that list.

## What the LLM is for

- Identify missing manual evidence (settlement-source URL, fee model,
  time zone, payoff shape) the deterministic ingester didn't surface.
- Flag fake-edge risks (title similarity, deadline-touch vs PIT,
  range vs threshold, ATH-by-date vs PIT).
- Suggest a relationship-type correction from the allowed taxonomy.
- Suggest additional manual checks (exact URLs / text to capture).
- Choose a `confidence_bucket` (`low`/`medium`/`high`).
- Always set `do_not_use_for_exact_gate=true`.

## What the LLM is NOT for

| Forbidden                                              | Why                                                                  |
| ------------------------------------------------------ | -------------------------------------------------------------------- |
| Claiming `PAPER_CANDIDATE`                             | Graph never creates evaluator inputs.                                |
| Setting `executable=true`                              | Graph never authorises trading.                                      |
| Setting `exact=true`                                   | RV's strict gate is the only path to same-payoff equivalence.        |
| Dropping deterministic blockers                        | LLM may *add* blockers; never remove them.                           |
| Lifting a weak-signal row above `low` confidence       | Title similarity stays low-confidence.                               |
| Upgrading a reference-only row to near-exact review    | Reference sources are never executable counterparts.                 |
| Recommending order placement / account ops             | Graph repo has no trading code.                                      |

## Workflow

1. Run `scan.py graph-manual-relationship-evidence` against the RV
   reports directory.
2. Run `scan.py llm-review-graph-manual-evidence` to produce a bounded
   prompt + strict JSON schema.
3. Paste the prompt into Claude / GPT and save the LLM response.
4. Run `scan.py validate-llm-graph-manual-evidence-review` — the
   validator either ACCEPTS the rows (adds them to the advisory
   layer) or REJECTS each row with the structural error.

## Output schema constraints

Each `reviewed_records[*]` row must contain (and only contain):

- `relationship_id`
- `diagnostic_only=true`
- `affects_evaluator_gates=false`
- `suggested_relationship_type` (enum from the manual-evidence taxonomy)
- `suggested_blockers` (non-empty list of strings)
- `suggested_manual_checks` (list of strings)
- `reviewer_notes` (string)
- `confidence_bucket` (`low`/`medium`/`high`)
- `do_not_use_for_exact_gate=true`

Any other field in the LLM output is rejected as a structural error.

## Companion docs

- `docs/GRAPH_MANUAL_DISCOVERY_PLAYBOOK.md` — how to run the backlog
  and what each field means.
- `docs/GRAPH_RELATIONSHIP_LLM_REVIEW.md` — the parallel LLM review for
  RV-edge graph relationships (the layer below this one).
- `docs/RV_GRAPH_LLM_BRIDGE_CONTRACT.md` — the cross-repo contract that
  keeps graph LLM and RV LLM workflows aligned and safe.
