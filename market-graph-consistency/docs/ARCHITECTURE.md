# Architecture

This repository is a lean, offline semantic consistency scanner.

## Flow

1. `scan.py` loads fixture markets from `venues/fixtures/`.
2. `graph_engine.loader` builds a `GraphSnapshot`.
3. `graph_engine.relationships.registry` loads manual relationship files from `relationships/`.
4. `graph_engine.consistency.runner` applies v1 checks.
5. `graph_engine.reporting` writes JSON and Markdown reports to `reports/`.
6. `graph_engine.reporting.hints` writes a graph-local relative-value hints artifact for later research review only.

Saved snapshot prototype mode uses `scan.py --snapshots-dir` or `--snapshot-file` to load schema-v1 normalized snapshot JSON files through `graph_engine.snapshot_loader`. It does not load manual fixture relationships or infer new relationships.

## Module Responsibilities

- `graph_engine/models.py`: dataclasses, enums, validation, serialization.
- `graph_engine/loader.py`: fixture-only market loading and snapshot construction.
- `graph_engine/snapshot_loader.py`: read-only schema-v1 saved snapshot loading.
- `graph_engine/relationships/types.py`: relationship model exports.
- `graph_engine/relationships/registry.py`: relationship file loading and market-id validation.
- `graph_engine/relationships/confidence.py`: simple confidence math.
- `graph_engine/relationships/llm_extractor.py`: deterministic offline interface stub.
- `graph_engine/consistency/checks.py`: implication, subset, rewording, exclusion, and ambiguous-wording checks.
- `graph_engine/consistency/tolerances.py`: v1 tolerances and action ladder.
- `graph_engine/reporting/json_report.py`: structured report writer.
- `graph_engine/reporting/md_report.py`: human-readable grouped report writer.
- `graph_engine/reporting/hints.py`: research-only graph hint export; not an evaluator integration.
- `graph_engine/semantics/`: minimal future helper functions.
- `graph_engine/backtest/`: documented placeholders for replay work.

## Invariants

- Offline by default and currently offline only.
- Fixtures are the only market source.
- Relationship files are hand-authored.
- Highest action is `MANUAL_REVIEW`.
- No account, execution, private API, scheduler, DB, or cross-repo imports.
- Graph contradictions and hints are not permission for orders or promoted candidate labels.

## Safety Vocabulary

- `action_permission`: explicit boolean on imported advisory hypotheses. It must be `false`; anything else is rejected or ignored before report writing.
- `review_blockers`: concrete missing evidence or safety blockers that must be resolved by an independent review path before a relationship can be used outside this graph.
- `why_review_only_yet`: plain-language explanation that a signal is diagnostic review material only and not an evaluator input.
- `non_actionable_input`: marks prices, midpoints, freshness data, or other inputs as diagnostic-only observations.
- `yes_price_equals_midpoint`: when a node's `yes_price` matches `(bid + ask) / 2` within float epsilon, the probability is treated as a synthesized midpoint, not a real trade. Constraint/signal rows mark `uses_yes_price_equal_to_midpoint=true` and the ops-status surfaces a dedicated count so reviewers do not mistake fixture-synthesized prices for actionable last-trade prices.
- `llm_evidence_role`: labels imported model output as `llm_hypothesis_advisory`, never as deterministic proof.
- `affects_evaluator_gates=false`: required report field confirming the graph artifact does not alter relative-value evaluator gates.

LLM agreement is recorded as corroborating context only. It does not add numeric severity to graph signals, and stale/lag or thematic hypotheses cannot strengthen structural confidence.

## Stale/Lag Watchlist

`graph_engine.reporting.stale_lag_watchlist` writes `reports/market_graph_stale_lag_watchlist.json` and `.md` during the default scan. It is saved-file-only and requires deterministic evidence before a row is counted as `WATCH`: the stale market must be older than 30 minutes, the related market must be fresher than 5 minutes, the probability difference must be at least 0.10, and the pair must share fixture-declared family metadata or a deterministic relationship edge. Missing timestamps, missing related markets, midpoint fallback, and synthesized yes-price inputs produce blockers instead of watch rows. LLM stale/lag hypotheses can only appear as co-witness context and cannot create a row by themselves.

## Ops Status Daily Surface

`graph_engine.reporting.ops_status` writes `reports/market_graph_ops_status.json` and `.md` after every default scan. It is the single daily operator surface and consumes saved trade indicator, probability constraint, payoff-state feasibility bridge, signal persistence, RV investigation packet, stale/lag watchlist, platform expansion radar, and event/entity ontology reports. The summary counts persistent high-confidence signals, midpoint/synthetic price rows, stale/lag watch and blocked rows separately, RV handoff packets ready, platform expansion gap rows (and HIGH-value subset), and ontology entity coverage. `stale_blocked_signal_constraint_packet_rows` only counts signal/constraint/packet rows blocked by stale or missing quote inputs so the stale/lag dedicated metric is not double counted. `yes_price_equal_to_midpoint_rows` counts both signals and constraints that use the synthesized midpoint. The recommended-actions ladder includes `REVIEW_PLATFORM_EXPANSION_GAPS` whenever HIGH-value gap rows exist and `REVIEW_ONTOLOGY_COVERAGE` whenever low-confidence entities or missing family coverage exists.

## Probability Constraint Interval Bounds

`graph_engine.reporting.probability_constraints` adds an `interval_bound_check` to each `subset_superset` row when both markets supply bid and ask quotes. The conservative gap pairs the highest plausible subset probability with the lowest plausible superset probability, while the optimistic gap pairs the lowest plausible subset with the highest plausible superset. A violation is flagged as `interval_violation_robust_to_bid_ask_uncertainty` only when even the optimistic gap exceeds tolerance; otherwise the inequality breach could be a midpoint artefact and must remain a review item rather than evidence of robust price divergence.

## Payoff Feasibility Counts in Ops Status

`graph_engine.reporting.ops_status` now surfaces the full payoff-state feasibility bridge breakdown: `bridge_row_count`, `bridge_feasible_count`, `bridge_infeasible_diagnostic_count`, and four `bridge_blocked_*` counts (missing payoff matrix, missing probability inputs, missing state family, unsupported constraint type). The dedicated `top_infeasibility_diagnostic` section ranks INFEASIBLE_DIAGNOSTIC bridge rows by `infeasibility_gap` descending and renders state family, status, blockers, and the review-only justification. `REVIEW_TOP_INFEASIBILITY` is added to the next-actions ladder whenever `bridge_infeasible_diagnostic_count > 0`.

## BTC Basis-Risk Packet Kind

`graph_engine.reporting.relative_value_investigation_packets` introduces a `packet_kind` field with values `STRUCTURAL_VIOLATION`, `LLM_ONLY`, `SIMILARITY_RESEARCH`, and `BTC_BASIS_RISK_REVIEW`. The `BTC_BASIS_RISK_REVIEW` kind triggers only when every market in a packet parses as `BTC_THRESHOLD` with identical date, threshold, comparator, and compatible window/unit, but settlement_source values differ across at least two markets and every settlement_source is a known (non-empty, non-unknown) basis source. The kind never claims exact same-payoff, exact arbitrage, or executable status; it is always routed to `MANUAL_REVIEW`, capped at `LLM_ONLY_CAP=45` priority, and carries a `requires_basis_source_distinction` blocker plus the standard required-evidence/disallowed-shortcuts contract. Packets containing a typed_formula_match_review_only relationship marker are excluded so this kind never overlaps with the structural-equality path.

## Ontology Entity Cross-Links in Packets

RV investigation packets now optionally carry `entity_ids` populated from the saved event/entity ontology when supplied. Entity IDs are navigation/review metadata only — they do not boost packet priority, do not satisfy evaluator gates, and are not identity proof. LOW-confidence (title-only or LLM-only) ontology rows still carry their `not_identity_proof_reason` and remain advisory.

## Stale/Lag Uniform-Timestamp Blocker

`graph_engine.reporting.stale_lag_watchlist` adds a `uniform_fixture_or_snapshot_timestamps_no_skew_detectable` blocker to any pair whose two quote ages differ by at most 60 seconds, and exposes `uniform_timestamps_blocked_count` at the report root. The ops status mirrors this as `uniform_timestamp_stale_blocked_count`. This compresses noisy fixture/uniform-snapshot scenarios where every pair would otherwise emit identical `timestamp_skew_below_threshold` blockers without hiding genuinely asymmetric skew cases (a 3601s-vs-60s pair still passes through).

## Family Inference Hardening

`graph_engine.reporting.platform_expansion_radar._infer_family` no longer treats a bare `threshold` or `range` token as evidence of BTC or FED family. The new `market_formulas` field attached to probability constraints and signal rows universally injects the literal word `threshold` (and a `GENERIC_THRESHOLD` family label) into row text for every threshold-shaped market. The fallback now requires both a structural keyword (`threshold_ladder`, `range_bucket`) and an explicit BTC/Bitcoin/FOMC/FED token; otherwise the row is classified as `UNKNOWN` so that non-BTC threshold markets like OpenAI valuation or AGI markets do not silently pollute BTC_THRESHOLD venue coverage.

## Market Shape Ontology (Design Memo)

`docs/MARKET_SHAPE_ONTOLOGY.md` is a design-only memo proposing the typed
domain x shape taxonomy and the six review-only relationship classes
(`EXACT_PAYOFF_EQUIVALENCE_REVIEW`, `BASIS_RISK_REVIEW`,
`ONE_SIDED_DOMINANCE_REVIEW`, `FAIR_VALUE_REFERENCE_ONLY`,
`CORRELATED_THEMATIC_WATCH`, `DISCOVERY_ONLY`) that the graph should use to
map markets across Kalshi, Polymarket, Crypto.com Predict / CDNA,
IBKR / ForecastEx, SX Bet, ProphetX, and The Odds API reference data. It
records evidence requirements per class, the unknown-shape clustering plan,
and the rule that the graph's cross-venue relationship registry stays
separate from RV's canonical source registry. The memo proposes no code
changes and preserves the `diagnostic_only`,
`allowed_actions=["WATCH","MANUAL_REVIEW"]` boundary.

## Fair-Value Relationships (Design Memo)

`docs/FAIR_VALUE_RELATIONSHIPS.md` complements the market-shape ontology by
defining six review-only fair-value relationship classes
(`BASIS_RISK_REVIEW`, `ONE_SIDED_DOMINANCE`, `RANGE_CONTAINMENT`,
`WINDOW_MISMATCH_FV`, `REFERENCE_ONLY_FV`, `CORRELATED_SIGNAL_ONLY`) and
the seven typed `dominance_basis_risk_class` labels
(`endpoint_dominance`, `interior_dominance`, `monthly_extreme_dominance`,
`path_dependent_dominance`, `range_subset_dominance`,
`nested_subset_dominance`, `same_window_different_source_dominance`). It
specifies per-domain rules for crypto (CDNA U-BTC vs Kalshi BRTI vs
Polymarket Binance 1m), sports (SX Bet vs Kalshi game-level; The Odds
API as reference-only), and politics/tech (election winner vs certified
result vs officeholder), the `fv_relevance_score` and
`overall_priority_score` formulas, and the strict split between
graph-only diagnostics and RV handoff packets. The memo proposes no code
changes and preserves the `diagnostic_only`,
`allowed_actions=["WATCH","MANUAL_REVIEW"]` boundary.

## Known Limits

V1 does not infer relationships, handle transitive closure, fetch live prices, or prove economic opportunity. It only flags fixture inconsistencies for human inspection.
