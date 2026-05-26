# Graph-to-Relative-Value Bridge Contract

This doc is the authoritative contract for what the market graph may hand to
the relative-value scanner and what the rel-value scanner must independently
verify before any rel-value evidence is trusted.

It is **diagnostic-only** infrastructure. Nothing on the bridge is permission
for orders, paper candidacy, executable claims, dollar profit claims, or
treating subset/superset/title-similarity/reference-only sources as exact
same-payoff.

The bridge spans two repositories:

- `market-graph-consistency/` — produces graph diagnostics and packet hints.
- `relative-value-scanner/` — independently re-verifies any handoff and
  decides whether it can become rel-value evidence.

If those two roles ever blur, this contract is broken.

## What the graph may export

The graph exposes saved-file artifacts under `reports/` (all diagnostic):

| Artifact | Purpose | Allowed actions |
| --- | --- | --- |
| `market_graph_relative_value_hints.json` | Edge-level relationship hints for review-only research. | `WATCH`, `MANUAL_REVIEW` |
| `graph_to_relative_value_investigation_packets.json` | Packaged review prompts spanning signals, constraints, and hypotheses. | `WATCH`, `MANUAL_REVIEW` |
| `market_graph_ops_status.json` | Daily operator surface with counts, top blockers, and recommended next actions. | `WATCH`, `MANUAL_REVIEW` |
| `market_graph_stale_lag_watchlist.json` | Deterministic stale/lag pairs with freshness buckets. | `WATCH`, `MANUAL_REVIEW` |
| `market_graph_payoff_state_feasibility_bridge.json` | Finite-state feasibility diagnostics with per-contract repair gaps. | `WATCH`, `MANUAL_REVIEW` |
| `market_graph_probability_constraints.json` | Probability-bound constraints by structural family. | `WATCH`, `MANUAL_REVIEW` |
| `market_graph_event_entity_ontology.json` | Entity/event metadata cross-links. | `WATCH`, `MANUAL_REVIEW` |
| `market_graph_platform_expansion_radar.json` | Platform/venue gap recommendations. | `WATCH`, `MANUAL_REVIEW` |

Every artifact carries the same fail-closed envelope:

```json
{
  "diagnostic_only": true,
  "affects_evaluator_gates": false,
  "allowed_actions": ["WATCH", "MANUAL_REVIEW"]
}
```

Any artifact that fails to carry that envelope must be rejected by both ends
of the bridge.

## What the graph may never export

Schema validation in
`graph_engine/reporting/schema_validation.py` and the prohibited-token
sentinels in `graph_engine/reporting/safety.py` reject any of the following
appearing anywhere in a generated report (keys, values, Markdown):

- `PAPER_CANDIDATE`, `paper_candidate`, `paper-candidate`, `is_paper_candidate_v2`, etc.
- `EXECUTE`, `executable`, `executable_arb`, `place_order`, `cancel_order`,
  `trade`, `trade_permission`, `order`, `fill`, `fill_size`, `size_usd`,
  `dollars`, `pnl`, `profit`, `profit_usd`, `edge_bps`, `signing`, `signature`,
  `wallet`, `private_key`, `position`.
- `POSSIBLE_ARB`, `arb` (single-word), `EXACT_SAME_PAYOFF`, `exact_same_payoff`,
  `trusted_relationship`, `evaluator_ready`, `buy`, `sell`.

Substring tightness is intentional — the prohibition catches `graph_hint_is_paper_candidate_v2` and `is-exact-same-payoff` even if a future module tries to bury the term inside a compound name.

In addition:

- **Relation types**: `EXACT_SAME_PAYOFF` is in the
  `DISALLOWED_HINT_RELATION_TYPES` set and is rejected by every hint validator.
- **Structural relations** (`SUBSET`, `SUPERSET`, `COMPLEMENT`,
  `MUTUALLY_EXCLUSIVE`, `EXHAUSTIVE_GROUP`) may never carry
  `hard_bound_type == "same_payoff_equality_if_settlement_proven"`. Subset/superset is a probability-bound diagnostic, not exact-payoff evidence.
- **`SAME_PAYOFF` relations** (rewording hypotheses) require
  `settlement_source_proven == true` and the
  `same_payoff_equality_if_settlement_proven` bound type, but they remain
  diagnostic and capped at `MANUAL_REVIEW`.
- **Reference-only venues** (currently The Odds API) must be routed through
  the `FAIR_VALUE_REFERENCE_ONLY` packet kind, capped at priority
  `LOW_PRIORITY_CAP == 35`, and routed to `IGNORE_LOW_CONFIDENCE` — never to
  `MANUAL_REVIEW` as if executable.
- **BTC basis-risk** is routed through `BTC_BASIS_RISK_REVIEW` and capped at
  `LLM_ONLY_CAP == 45` even when terms align on date, threshold, comparator,
  and window.

The hint test file
[`tests/test_relative_value_hint_schema.py`](../tests/test_relative_value_hint_schema.py)
and the action guardrail tests in
[`tests/test_action_guardrails.py`](../tests/test_action_guardrails.py) enforce
these rules. A future
`tests/test_report_corpus_no_prohibited_tokens.py` walks every JSON and
Markdown file in `reports/` to catch regressions that bypass per-module
validation.

## What rel-value must independently verify

The rel-value scanner must treat every graph packet as a *hypothesis*. Before
the rel-value scanner converts any graph artifact into rel-value evidence it
**must independently re-verify**:

1. **Typed keys**: parse each market's settlement/observable/threshold/window
   keys directly from the source. Do not trust the graph's
   `market_formulas` row.
2. **Settlement source proof**: confirm the resolution data source matches
   across all legs. Sources like "BRTI vs Coinbase index" are not equivalent.
3. **Payoff relationship proof**: prove the legs really sit in the claimed
   structural relationship (subset, complement, exhaustive partition, etc.) —
   the graph supplies a hypothesis, not proof.
4. **Complement / subset / exhaustive proof**: when the relation depends on
   completeness of an outcome list, prove the list is exhaustive from
   venue-native metadata, not graph counts or title-similarity.
5. **Orderbook depth & freshness**: pull current orderbook depth and quote
   timestamp per leg. The graph never has executable depth.
6. **Fee model**: include venue-native maker/taker fees, withdrawal/transfer
   fees, and settlement currency conversions per leg.
7. **Unit / currency / collateral mechanics**: confirm both legs settle in
   compatible units (USD vs USDC vs DAI vs sports stake size etc.).
8. **Void / cancellation rules**: confirm the legs cannot diverge under
   one-sided void/postpone/forfeit/rain-out rules.

`REQUIRED_EVIDENCE_BEFORE_RV_REVIEW` in
`graph_engine/reporting/relative_value_investigation_packets.py` enumerates
the same list. Every packet validator enforces the list is present and
intact.

## What the graph promises about packet identity

- Every packet has `packet_kind ∈ {STRUCTURAL_VIOLATION, LLM_ONLY,
  SIMILARITY_RESEARCH, BTC_BASIS_RISK_REVIEW, FAIR_VALUE_REFERENCE_ONLY}`.
- Every packet has `allowed_next_action ∈ {MANUAL_REVIEW,
  BUILD_TYPED_KEY_EXTRACTOR, BUILD_SETTLEMENT_SOURCE_REGISTRY_ENTRY,
  FETCH_OR_ENRICH_ORDERBOOKS, IGNORE_LOW_CONFIDENCE}`.
- Every packet carries a `disallowed_shortcuts` list — the rel-value scanner
  must reject any handoff that omits or alters this list.
- Reference-only packets must carry the `reference_only_source` blocker and
  must route to `IGNORE_LOW_CONFIDENCE`.

## How rel-value uses graph hints

The rel-value scanner may use packets and ops-status counts to:

- Prioritize which markets it pulls into its own canonical convention registry.
- Decide which families to ask for a typed-key extractor first.
- Note which platform/venue gaps the radar flags as high value.

The rel-value scanner **must not**:

- Promote a graph packet directly to a rel-value paper candidate.
- Treat a `SAME_PAYOFF` graph hint as same-payoff proof.
- Treat a `SUBSET`/`SUPERSET` graph hint as exact same-payoff proof.
- Treat reference-only sources (The Odds API, CME implied probability, BTC
  options implied probability, weather forecasts) as executable inputs.
- Skip its own exact-payoff / settlement / depth / freshness / fee
  verification because the graph already labeled the markets as related.

## Where the boundary is enforced

Hard rules (cannot be bypassed without changing both repos and breaking tests):

- `graph_engine/reporting/safety.py` — central prohibited vocabulary.
- `graph_engine/reporting/schema_validation.py` — schema-level structural
  invariants.
- `graph_engine/reporting/hints.py` — graph hint export schema.
- `graph_engine/reporting/relative_value_investigation_packets.py` — RV
  packet kind invariants and required-evidence list.
- `graph_engine/reporting/ops_status.py` — daily radar schema invariants and
  top-blocker suppression of tautological invariants.
- `relative-value-scanner/` — must independently verify and never weaken its
  exact-payoff / evaluator gates because the graph said anything.

## Operator workflow

1. Run `python scan.py` from `market-graph-consistency/` to refresh all graph
   reports.
2. Open `reports/market_graph_ops_status.md` and read in this order:
   - Summary block (signals, freshness buckets, packet-kind counts).
   - Top Blockers (what to fix first to unblock the most rows).
   - Next Recommended Actions (which review track to pick up first).
   - Top Persistent High Confidence / Top Probability Constraints /
     Top RV Handoff Packets (concrete review items).
3. Pick a row, open the underlying source report listed in the row, and walk
   the markets into rel-value as a *review request*.
4. Inside rel-value, run the independent verification list above before any
   rel-value evidence is recorded.

The graph is a discovery / prioritization / fail-closed surface. Rel-value is
the truth gate. The bridge is the contract that keeps them honest.
