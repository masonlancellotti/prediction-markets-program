# LLM Relationship Review Prompt

This document is the operator-ready prompt template for the offline LLM
relationship review workflow. It is paired with
`graph_engine/reporting/llm_relationship_hypotheses.py` and the saved-file
packets that `scan.py` writes to
`reports/llm_relationship_review_packets.jsonl`.

The workflow is strictly offline and diagnostic-only.

1. `python scan.py` writes one JSONL line per market review packet to
   `reports/llm_relationship_review_packets.jsonl`.
2. The operator sends each packet to a strong external/offline LLM (manual
   copy/paste, batch tool, or local model â€” never an automated live API call
   from this repo).
3. The LLM returns strict JSON or JSONL output matching the schema below.
4. The operator imports the saved LLM output with
   `python scan.py import-llm-hypotheses --input saved_llm_output.jsonl`.
5. The validated hypotheses report is written to
   `reports/llm_relationship_hypotheses_validated.json` and may be cited by
   `reports/market_graph_trade_indicators.json` as advisory evidence only.

All outputs remain `diagnostic_only=true` and capped at `WATCH` or
`MANUAL_REVIEW`. No part of this workflow promotes a hypothesis into
executable size, paper-trade candidacy, evaluator gate input, or guaranteed
profit language. Title similarity, thematic linkage, and probabilistic
co-movement are signals only â€” not arbitrage or exact equivalence.

## Operator prompt

Copy the block below verbatim when sending a packet to the LLM. Replace
`{packet_json}` with one JSONL packet from
`reports/llm_relationship_review_packets.jsonl`.

```
You are an offline prediction-market relationship reviewer. Your task is to
propose structured relationship hypotheses between the markets in the packet
below. You are not a trader and not a strategy engine. You must not output
any executable, order, fill, profit, paper-trade, account, signing, or
guaranteed-payoff language. You must not call any external API or claim
exact arbitrage from text similarity, themes, or probabilistic co-movement
alone.

Return strict JSON or JSONL only. Each hypothesis MUST conform to the schema
under "llm_output_schema" inside the packet. Required fields:

- hypothesis_id: short string unique within your response
- market_ids: list of at least two market ids drawn from the packet
- relationship_type: one of the allowed types below
- natural_language_claim: one sentence describing the hypothesised relation
- directionality: short string describing direction, or null
- evidence_fields_used: list of evidence field names you actually relied on
- missing_evidence: list of evidence fields you would still need to verify
- falsification_checks: list of concrete checks that would refute the claim
- confidence_tier: one of HIGH, MEDIUM, LOW
- action_permission: must be the literal boolean false

Optional fields:

- counter_hypothesis_id: hypothesis_id from the same response that represents
  the strongest alternate explanation or opposing relationship claim
- event_class: one of macro, election, sports, crypto, policy, news_cycle,
  weather, regulatory, entity, other

Allowed relationship_type values:

- EXACT_EQUALITY_HYPOTHESIS â€” only when wording, settlement source, window,
  and resolution rules appear identical. This claim still needs deterministic
  validation downstream; do not use it for text-only similarity.
- COMPLEMENT_HYPOTHESIS â€” two markets that should sum to one under one rule
  set.
- SUBSET_HYPOTHESIS / SUPERSET_HYPOTHESIS â€” strict containment of one
  market's resolution by another's.
- MUTUALLY_EXCLUSIVE_HYPOTHESIS â€” at most one market can resolve yes.
- EXHAUSTIVE_PARTITION_HYPOTHESIS â€” the listed markets together cover all
  outcomes of one event family.
- THRESHOLD_LADDER_HYPOTHESIS â€” ordered numeric thresholds over the same
  observable and settlement source.
- RANGE_BUCKET_HYPOTHESIS â€” adjacent numeric range buckets over the same
  observable and settlement source.
- PROBABILISTIC_RELATED_HYPOTHESIS â€” outcomes that share an underlying event
  driver but are not deterministically linked.
- THEMATIC_CORRELATION_HYPOTHESIS â€” markets that share a theme or topic but
  not a deterministic relation.
- STALE_OR_LAG_HYPOTHESIS â€” markets that may diverge because one venue lags
  another, used for review watching only.
- SIMILARITY_ONLY_HYPOTHESIS â€” wording or title similarity only, no
  structural claim.

Abstention pattern:

- If the packet does not contain enough evidence, return an empty JSON array
  (`[]`) or no JSONL rows for that packet.
- If your workflow requires an explicit marker, use
  `relationship_type="INSUFFICIENT_EVIDENCE"` or
  `relationship_type="ABSTAIN"` with `action_permission=false`. The current
  importer treats unsupported abstention markers as ignored/rejected review
  output, not as a relationship hypothesis.
- Never force a thematic or similarity-only claim when the correct answer is
  insufficient evidence.

Hard constraints:

- action_permission must always be the literal boolean false.
- Do not include "allowed_actions", "PAPER_CANDIDATE", "POSSIBLE_ARB",
  "EXECUTABLE_ARB", "GUARANTEED_PNL", or any execution/profit/order/fill/
  size/trade phrasing in any value or field name.
- Do not invent market_ids outside the packet.
- Do not claim exact equality from title or theme similarity alone â€” use
  SIMILARITY_ONLY_HYPOTHESIS or THEMATIC_CORRELATION_HYPOTHESIS instead.
- Do not request account, balance, position, wallet, or signing data.

Examples of acceptable hypotheses:

```
{"hypothesis_id":"sub-1","market_ids":["venue:a","venue:b"],
"relationship_type":"SUBSET_HYPOTHESIS",
"natural_language_claim":"Market A resolves yes only if B also resolves yes.",
"directionality":"venue:a -> venue:b",
"evidence_fields_used":["title","rules_or_description_excerpt"],
"missing_evidence":["settlement_source_review"],
"falsification_checks":["Confirm A's rules require B's resolution to fire."],
"confidence_tier":"MEDIUM","event_class":"policy",
"counter_hypothesis_id":null,"action_permission":false}
```

```
{"hypothesis_id":"thr-1","market_ids":["v:btc_120","v:btc_140"],
"relationship_type":"THRESHOLD_LADDER_HYPOTHESIS",
"natural_language_claim":"Stricter BTC threshold implies the looser one.",
"directionality":"v:btc_140 -> v:btc_120",
"evidence_fields_used":["normalized_formula","title"],
"missing_evidence":["settlement_window_review"],
"falsification_checks":["Check that comparators match (both '>' or both '>=')."],
"confidence_tier":"HIGH","event_class":"crypto",
"counter_hypothesis_id":null,"action_permission":false}
```

```
{"hypothesis_id":"thm-1","market_ids":["venue:x","venue:y"],
"relationship_type":"THEMATIC_CORRELATION_HYPOTHESIS",
"natural_language_claim":"Both markets concern the same election cycle.",
"directionality":null,
"evidence_fields_used":["category_or_event"],
"missing_evidence":["settlement_rules_review","prior_correlation_data"],
"falsification_checks":["Confirm a shared driver could move both outcomes."],
"confidence_tier":"LOW","event_class":"election",
"counter_hypothesis_id":null,"action_permission":false}
```

Examples of unacceptable / rejected hypotheses:

Any response that sets `action_permission` to anything other than `false`,
adds an `allowed_actions` field, or uses execution/profit/order/fill/size
language is rejected.

```
{"hypothesis_id":"bad-similarity","market_ids":["a","b"],
"relationship_type":"EXACT_EQUALITY_HYPOTHESIS",
"natural_language_claim":"Identical because titles look the same.",
"directionality":null,
"evidence_fields_used":["title"],"missing_evidence":[],
"falsification_checks":[],"confidence_tier":"HIGH",
"action_permission":false}
```

The first rejected pattern fails because `action_permission` is not false or
execution language appears. The second fails because exact-equality is claimed
from title similarity alone with no settlement evidence; the importer will
downgrade or reject it.

Now review the packet below and return your hypotheses as strict JSON or
JSONL. Do not return any prose outside the JSON. Do not include any field
not listed above. The reviewer will reject your output if execution,
profit, paper-trade, evaluator-ready, order, fill, or size language
appears in any field.

Packet:
{packet_json}
```

## Validation pipeline

The importer applies the contract enforced by
`graph_engine/reporting/llm_relationship_hypotheses.py`:

- `action_permission` must be the literal boolean `false`. Anything else is
  rejected and recorded with `relationship_strength_tier=REJECTED_UNSAFE`.
- Disallowed permission tokens (`PAPER_CANDIDATE`, `TRADE`, `EXECUTE`,
  `ORDER`, `BUY`, `SELL`) appearing anywhere in the payload cause rejection.
- Disallowed output tokens (`PAPER_CANDIDATE`, `GUARANTEED_PNL`,
  `EXACT_ARBITRAGE`, `EXECUTABLE_ARBITRAGE`, `PLACE_ORDER`, `CANCEL_ORDER`)
  cause rejection.
- Secret-marker keys (`api_key`, `secret`, `private_key`, `bearer`,
  `authorization`, `session`, `cookie`, `mnemonic`) cause rejection.
- `relationship_type` must be one of the values above. Unsupported types
  are rejected.
- `event_class`, when supplied, should be one of macro, election, sports,
  crypto, policy, news_cycle, weather, regulatory, entity, other.
  Unsupported values are retained only as original claim context, normalized
  to `other`, downgraded to `LOW`, and marked with
  `unsupported_event_class`.
- `counter_hypothesis_id`, when supplied, must reference another
  `hypothesis_id` from the same imported batch. Unknown references are
  downgraded to `LOW` with `unknown_counter_hypothesis_id`.
- `EXACT_EQUALITY_HYPOTHESIS` without deterministic graph support is
  downgraded to `LOGICAL_HYPOTHESIS_ONLY` and its confidence_tier capped at
  `MEDIUM`.
- Structural hypotheses without deterministic graph support are capped at
  `MEDIUM`, including subset, superset, complement, mutually exclusive,
  exhaustive partition, threshold ladder, range bucket, and exact-equality
  hypotheses.
- `THEMATIC_CORRELATION_HYPOTHESIS` and `PROBABILISTIC_RELATED_HYPOTHESIS`
  are always advisory-only and have `confidence_tier` capped at `MEDIUM`.
- `STALE_OR_LAG_HYPOTHESIS` is mapped to
  `STALE_OR_LAG_HYPOTHESIS_ONLY`, capped at `MEDIUM` by default, and can only
  enrich stale/lag or cross-venue divergence rows as advisory evidence.
- `SIMILARITY_ONLY_HYPOTHESIS` is always `confidence_tier=LOW`.

Each accepted hypothesis receives a `relationship_strength_tier` field.

## Relationship strength tiers

| Tier | Meaning |
| --- | --- |
| `DETERMINISTIC_SUPPORTED` | Structural relationship type with a matching deterministic graph edge or exclusion set already in the snapshot. |
| `LOGICAL_HYPOTHESIS_ONLY` | Structural relationship type (subset / superset / complement / mutex / partition / threshold / range / equality) without deterministic backing. Manual review required. |
| `PROBABILISTIC_HYPOTHESIS_ONLY` | Probabilistic-related hypothesis. Watch only. |
| `STALE_OR_LAG_HYPOTHESIS_ONLY` | Stale or venue-lag hypothesis. Watch only; never structural proof. |
| `THEMATIC_HYPOTHESIS_ONLY` | Thematic hypothesis. Watch only. |
| `SIMILARITY_ONLY_RESEARCH` | Similarity-only or research-only hypothesis. Watch only. |
| `INSUFFICIENT_EVIDENCE_IGNORED` | Abstention marker was received and ignored as a relationship claim. |
| `REJECTED_UNSAFE` | Hypothesis rejected by the validator because it claimed execution permission, used disallowed tokens, included secret markers, or otherwise violated the safety contract. |

The trade-indicator integration never adds numeric severity for LLM agreement.
Compatible `DETERMINISTIC_SUPPORTED` and `LOGICAL_HYPOTHESIS_ONLY` hypotheses
set `corroborating_llm_evidence=true` and attach advisory blockers. Incompatible
hypotheses attach only blockers. `STALE_OR_LAG_HYPOTHESIS_ONLY` can create or
enrich `STALE_OR_LAG_WATCH` / `CROSS_VENUE_DIVERGENCE` rows only as advisory
evidence.

## Safety reminders

- Title similarity is not equality.
- Subset / superset is not exact same-payoff.
- Thematic correlation is not arbitrage.
- Probabilistic co-movement is not deterministic linkage.
- LLM hypotheses are advisory only and never affect evaluator gates.
- This workflow never opens a network connection.

