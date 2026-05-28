# RV ↔ Graph LLM Bridge Contract

Two repositories collaborate on prediction-market relationships:

- **`relative-value-scanner`** — strict same-payoff / fees / depth /
  freshness gate.  Only the RV evaluator path can promote a market pair
  to a paper-candidate review.
- **`market-graph-consistency`** — diagnostic-only relationship memory.
  Remembers related markets, why they are related, and why they are
  not exact.  Routes the best non-exact candidates back to RV for
  strict review.

Both repos may use LLMs to extract insight from saved diagnostic
artifacts.  This file is the **bridge contract** that keeps the two
LLM workflows safe and aligned.

## Roles

| Side  | LLM role                                                                                     |
| ----- | -------------------------------------------------------------------------------------------- |
| Graph | Reviews graph relationship edges; suggests relationship type / blockers / manual checks.     |
| RV    | Reviews settlement source, threshold convention, and fee/depth/freshness evidence packets.   |

## Invariants

1. **Diagnostic only.** Neither LLM may emit `PAPER_CANDIDATE`,
   `executable=true`, or any direct trade instruction.
2. **No gate lowering.** Neither LLM may drop or weaken blockers from
   the deterministic side that produced the row.
3. **Schema-validated.** Each LLM output is validated against a strict
   JSON schema before it is persisted.  Rejected outputs are stored
   along with structural errors and the offending vocabulary.
4. **Blocker-preserving.** LLM rows that add new blockers are accepted;
   LLM rows that remove blockers are rejected.
5. **No mutual trust shortcut.** Graph LLM output is *advisory* and
   cannot become RV trusted evidence without independent RV review.
   RV LLM output is *advisory* and cannot become graph trusted edge
   evidence without independent graph review.
6. **Manual human review** is required for any *trusted* relationship —
   trusted in the sense that it has cleared both sides and a reviewer
   approved it.

## Handoff direction

The handoff is one-way:

```
graph relationship edges  ──►  graph-to-RV worklist  ──►  RV strict gate
   (deterministic + LLM)      (review-only routing)       (paper-candidate review)
```

Graph never short-circuits the RV strict gate, regardless of how
confident an LLM is.  The graph-to-RV worklist (`rv_review_worklist.md`)
exists only to suggest *what the RV scanner should inspect next*.

## Where exact same-payoff lives

**Only** in the relative-value-scanner strict board and evidence path.
Graph never claims `exact_payoff=true`, never sets
`can_create_candidate_pair=true`, and never emits an evaluator input.

## Vocabulary safety

The graph repo's `graph_engine/reporting/safety.py` enforces a banned-
vocabulary list (`paper_candidate`, `executable`, `arb`, `profit`,
`order`, etc.).  Any LLM output containing those tokens — in keys,
values, or rendered Markdown — is rejected with a structural error.

The same forbidden vocabulary applies to *suggested* relationship
types, blockers, manual checks, and notes.  If you need to convey a
related concept, use the graph-safe alias: `can_emit_evaluator_input`
for the paper-candidate semantic, `affects_evaluator_gates` for the
gating semantic, `same-payoff candidate review` rather than the
ungrammable phrase.
