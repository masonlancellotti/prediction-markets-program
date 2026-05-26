# Fair-Value Relationships (Design Memo)

Status: design memo. No code changes. All proposed graph outputs preserve
`diagnostic_only=true`, `affects_evaluator_gates=false`,
`allowed_actions=["WATCH","MANUAL_REVIEW"]`.

This memo defines how the graph should model *non-exact-but-economically-useful*
relationships across market shapes and venues. It builds on the typed
domain x shape taxonomy in [MARKET_SHAPE_ONTOLOGY.md](MARKET_SHAPE_ONTOLOGY.md)
and on the existing `BTC_BASIS_RISK_REVIEW` and `FAIR_VALUE_REFERENCE_ONLY`
packet kinds in `graph_engine/reporting/relative_value_investigation_packets.py`.

The motivation: today the graph only meaningfully ranks structural
violations of equality (subset, complement, exhaustive, threshold-ladder,
range-bucket). It does not surface relationships that are *known not to be
exact* but still useful as fair-value references — for example, Polymarket's
"BTC hits 120k by Dec-31" (deadline-hit-by-date) vs Kalshi's "BTC > 120k on
Dec-31 close" (point-in-time threshold), where the first dominates the
second by construction. Without a typed fair-value layer, those relationships
either disappear (no signal emitted) or get mislabeled as exact-equivalence
candidates (fake edge). This memo specifies the six fair-value classes, the
per-domain rules that emit them, the scoring shape, and the split between
what stays in graph vs what becomes an RV handoff packet.

## Class-name aliasing for safety vocabulary

The briefed class names pass the safety scanner unchanged:
`BASIS_RISK_REVIEW`, `ONE_SIDED_DOMINANCE`, `RANGE_CONTAINMENT`,
`WINDOW_MISMATCH_FV`, `REFERENCE_ONLY_FV`, `CORRELATED_SIGNAL_ONLY`.

For consistency with `EXACT_PAYOFF_EQUIVALENCE_REVIEW` /
`EXECUTABLE_LEG_OK` in [MARKET_SHAPE_ONTOLOGY.md](MARKET_SHAPE_ONTOLOGY.md),
the venue tag for tradable legs is `EXECUTABLE_LEG_OK` and the
exact-equivalence packet is `EXACT_PAYOFF_EQUIVALENCE_REVIEW`. Six fair-value
classes below stand alongside that exact-equivalence class as the seventh —
they are explicitly *non-exact* and never grant evaluator permission.

## Fair-value relationship taxonomy

Six classes. Each carries a `fv_relevance_score` (0..1) and a
`fake_edge_risk_class`. All classes cap at `MANUAL_REVIEW`.

| Class | Definition | Typical evidence | Output |
|---|---|---|---|
| `BASIS_RISK_REVIEW` | Same entity, same event-window, **different** settlement source. | Same `entity_id`, `event_id`, `operator`, `threshold`, `unit`; different `settlement_source`; both sources are `_known_basis_source`. | RV handoff packet (existing kind `BTC_BASIS_RISK_REVIEW` is one instance). |
| `ONE_SIDED_DOMINANCE` | P(A) >= P(B) by structural inclusion. A is deadline-hit; B is point-in-time at deadline end. A is monthly-extreme; B is end-of-month. A is "anytime in window"; B is "at instant within window". | Same entity, comparable window with explicit inclusion; same `operator`+`threshold`; explicit `dominance_basis_risk_class`. | Graph-internal diagnostic; RV handoff packet only when both legs are `EXECUTABLE_LEG_OK`. |
| `RANGE_CONTAINMENT` | Bucket B's `[lo,hi]` is contained inside bucket A's `[lo,hi]`; therefore P(B) <= P(A). | Same entity, same instant, monotone interval inclusion; same source. | Graph-internal first; surfaces in RV handoff when interval inclusion crosses venues. |
| `WINDOW_MISMATCH_FV` | Same entity and threshold, **different** settlement window (hour vs day vs deadline vs range). Path-dependent inequality may or may not hold. | Same entity+threshold; different settlement_window class; documented `window_relation` (`endpoint`, `interior`, `extreme`). | Graph diagnostic only; never RV handoff (signal-to-noise too low). |
| `REFERENCE_ONLY_FV` | One side is a reference feed (`executable_class=REFERENCE_ONLY`) that cannot be a tradable leg. | Source listed in `REFERENCE_ONLY_VENUES` or row carries `reference_only_source` blocker. | Graph diagnostic; ops_status counter; RV handoff only as fair-value context with `reference_only_fv_not_executable_leg` blocker. |
| `CORRELATED_SIGNAL_ONLY` | Markets move together due to shared underlying theme but payoff is not structurally tied. | Shared entity or theme; explicit `non_equivalence_acknowledged=true`. | Graph diagnostic only; never RV handoff. |

These six classes plus `EXACT_PAYOFF_EQUIVALENCE_REVIEW` and `DISCOVERY_ONLY`
(from MARKET_SHAPE_ONTOLOGY.md) form the closed set of relationship classes
the graph emits.

## Dominance relationship taxonomy

`ONE_SIDED_DOMINANCE` and `RANGE_CONTAINMENT` carry a typed
`dominance_basis_risk_class`. The label says *why* one side dominates and
how much path-dependent slack the inequality permits:

| `dominance_basis_risk_class` | Meaning | Tight inequality? | Typical FV slack |
|---|---|---|---|
| `endpoint_dominance` | "Anytime in window" includes "at endpoint of window". Inequality holds exactly. | Yes (1.0) | 0 |
| `interior_dominance` | "Anytime in window" includes "at named interior point". Inequality holds exactly. | Yes (1.0) | 0 |
| `monthly_extreme_dominance` | Monthly max/min over the month always >= / <= same-asset end-of-month value. | Yes (1.0) | 0 |
| `path_dependent_dominance` | Deadline-hit vs point-in-time. P(any_time_hit) >= P(at_endpoint_hit) holds exactly; FV gap = (path_density - endpoint_density), depends on vol regime. | Yes (1.0) | Vol-dependent |
| `range_subset_dominance` | Bucket B's [lo,hi] subset of bucket A's [lo,hi]. P(A) >= P(B) exact. | Yes (1.0) | 0 |
| `nested_subset_dominance` | Event E2 implies event E1 by language (e.g. "Trump 2028 winner" implies "Republican 2028 winner"). | Yes (1.0) under assumed entity equality | 0 unless entity match is loose |
| `same_window_different_source_dominance` | Same window/threshold, but different settlement source where one source is known to lead/lag the other. | No (correlational) | Source-dispersion-dependent |

Only the first six are emitted by the graph today; the last one is the
boundary case the graph must *not* cross — it slides into
`BASIS_RISK_REVIEW` (typed) or `CORRELATED_SIGNAL_ONLY` (untyped), and never
into `ONE_SIDED_DOMINANCE`.

## Crypto relationship rules

| Pair | Class | `dominance_basis_risk_class` | Notes |
|---|---|---|---|
| **Polymarket "BTC hits $X by D" vs Kalshi "BTC > $X on D close"** | `ONE_SIDED_DOMINANCE` | `path_dependent_dominance` | Polymarket dominates; FV slack increases with implied vol and time-to-D. |
| **Polymarket "BTC monthly high >= $X for month M" vs Kalshi "BTC > $X on month-M last day"** | `ONE_SIDED_DOMINANCE` | `monthly_extreme_dominance` | Polymarket dominates; gap = monthly max distribution vs single-day distribution. |
| **Polymarket "BTC hourly up" vs Kalshi "BTC > prior-hour-close at H+1"** | `EXACT_PAYOFF_EQUIVALENCE_REVIEW` if both use same Coinbase hourly close, otherwise `BASIS_RISK_REVIEW`. | n/a | Distinguish by `settlement_source` strictly. |
| **CDNA U-BTC vs Kalshi BRTI on same threshold/window** | `BASIS_RISK_REVIEW` | n/a | Different methodologies (CDNA U-BTC = Coinbase/Bitstamp/Kraken weighted; Kalshi BRTI = CME reference rate). |
| **Polymarket "Binance 1m candle close >= $X at T" vs Kalshi "BRTI >= $X at T"** | `BASIS_RISK_REVIEW` | n/a | Different methodologies plus different time-stamp interpretation (1m vs 1s snapshot). |
| **Polymarket range-bucket "BTC in [a,b] on D" vs Polymarket point-in-time "BTC > b on D"** | `RANGE_CONTAINMENT` | `range_subset_dominance` | Same venue, exact subset relationship. |
| **Coinbase price feed (reference) vs any Kalshi BTC market** | `REFERENCE_ONLY_FV` | n/a | Coinbase data is reference-only; never a tradable leg. |
| **BTC vs ETH same-threshold markets** | `CORRELATED_SIGNAL_ONLY` | n/a | Theme-correlated, not structurally tied. |

The `BTC_BASIS_RISK_REVIEW` packet kind in
`relative_value_investigation_packets.py` is already an instance of
`BASIS_RISK_REVIEW`; the new shapes here extend that pattern to the other
crypto venues.

## Sports relationship rules

| Pair | Class | Notes |
|---|---|---|
| **SX Bet game moneyline vs Kalshi same-game moneyline** | `EXACT_PAYOFF_EQUIVALENCE_REVIEW` if both settle on official game result and venue is `EXECUTABLE_LEG_OK`; otherwise `BASIS_RISK_REVIEW`. | Distinguish by `game_id` and `settlement_source`. |
| **SX Bet totals/spreads vs The Odds API consensus** | `REFERENCE_ONLY_FV` | The Odds API is never `EXECUTABLE_LEG_OK`. |
| **SX Bet game-level moneyline vs Kalshi season-futures championship** | `CORRELATED_SIGNAL_ONLY` (NOT dominance) | `sports_game_level_vs_futures_scope_mismatch` blocker required. |
| **The Odds API consensus moneyline vs SX Bet same-game moneyline** | `REFERENCE_ONLY_FV` with FV slack = consensus minus SX. | Reference side cannot be the recipient of a handoff. |
| **Player-prop "Player P >= N points" vs game total "Total >= T"** | `CORRELATED_SIGNAL_ONLY` | Often confused; share entity but not payoff. |
| **Cross-venue "Same team wins championship"** | `EXACT_PAYOFF_EQUIVALENCE_REVIEW` if both venues settle on official league result; else `BASIS_RISK_REVIEW`. | Common venues: Kalshi, Polymarket, ProphetX. |
| **Cross-venue futures vs game-level same team** | `CORRELATED_SIGNAL_ONLY` always. | Never promote to dominance — outcome paths diverge. |

The existing `sports_game_level_vs_futures_scope_mismatch` fake-edge label
stays in force as a hard blocker against accidental `ONE_SIDED_DOMINANCE`
promotion.

## Politics / tech relationship rules

| Pair | Class | Notes |
|---|---|---|
| **Election winner vs officeholder-at-inauguration-date** | `BASIS_RISK_REVIEW` (different settlement) or `ONE_SIDED_DOMINANCE` with `endpoint_dominance` only when source identifies death/recusal as settling the officeholder market in the winner's favor (rare; explicit reviewer note required). | Default to `BASIS_RISK_REVIEW`. |
| **Election winner vs certified-result winner** | `BASIS_RISK_REVIEW` | Same event; settlement source differs (AP/Decision Desk vs Electoral College / state certifications). |
| **Nomination winner vs election winner (same party)** | `ONE_SIDED_DOMINANCE` with `nested_subset_dominance` | P(wins election) <= P(wins nomination AND wins election) <= P(wins nomination). Multi-step inclusion. |
| **Election winner vs inauguration-occurs** | `CORRELATED_SIGNAL_ONLY` | Inauguration occurrence depends on health/withdrawal/transition; not a clean dominance. |
| **"Will X be SecState in 2026?" vs "Will X be SecState in 2027?"** | `CORRELATED_SIGNAL_ONLY` | Different windows; not structural dominance. |
| **AI model release-by-date vs broader "AGI by date" theme** | `CORRELATED_SIGNAL_ONLY` | Theme correlation only. |
| **Company stock price >= $X by D vs same company market-cap >= $Y by D** | `CORRELATED_SIGNAL_ONLY` unless mapping known via share-count snapshot. | Could become typed if a typed extractor proves the cap = price * shares relation. |
| **Product launch by D vs broader product roadmap mentions** | `DISCOVERY_ONLY` | Title overlap only; nothing structural. |
| **Single-candidate vs multi-candidate-OR market** | `RANGE_CONTAINMENT` (set-theoretic) with `nested_subset_dominance` | "Trump wins" subset of "Trump-or-DeSantis wins". |

## Scoring

Each fair-value relationship row carries:

```
fv_relevance_score          : float in [0, 1]
basis_risk_severity         : float in [0, 1]
dominance_direction         : "src_geq_dst" | "src_leq_dst" | "src_eq_dst" | "none"
evidence_quality_tier       : "HIGH" | "MEDIUM" | "LOW"
quote_quality_tier          : "FRESH" | "STALE" | "MIDPOINT_ONLY" | "MISSING"
reference_only_penalty      : float in [0, 1] (1.0 means full penalty, RV handoff blocked)
overall_priority_score      : float in [0, 100]
```

`overall_priority_score` = `100 * fv_relevance_score *
(1 - reference_only_penalty)
* evidence_weight(evidence_quality_tier)
* quote_weight(quote_quality_tier)` where the weights are:

- `evidence_weight`: HIGH=1.0, MEDIUM=0.6, LOW=0.3
- `quote_weight`: FRESH=1.0, STALE=0.5, MIDPOINT_ONLY=0.3, MISSING=0.0

`fv_relevance_score` is class-dependent:
- `EXACT_PAYOFF_EQUIVALENCE_REVIEW` = 1.0 when settlement-equality evidence is filed; 0.6 when typed but unfiled; 0 otherwise.
- `BASIS_RISK_REVIEW` = 0.8 when both sources are `_known_basis_source` with documented historical basis spread; 0.5 when one source is documented; 0 otherwise.
- `ONE_SIDED_DOMINANCE` = 0.9 for `endpoint_dominance` / `interior_dominance` / `range_subset_dominance` / `nested_subset_dominance`; 0.7 for `monthly_extreme_dominance`; 0.5 for `path_dependent_dominance` (vol-dependent slack).
- `RANGE_CONTAINMENT` = 0.85.
- `WINDOW_MISMATCH_FV` = 0.3 (low; mostly review).
- `REFERENCE_ONLY_FV` = 0.5 (FV input only).
- `CORRELATED_SIGNAL_ONLY` = 0.1.

`basis_risk_severity` is 0 for `EXACT_PAYOFF_EQUIVALENCE_REVIEW` and
`RANGE_CONTAINMENT`; for `BASIS_RISK_REVIEW` it equals the maximum observed
historical basis spread divided by the typical bid-ask spread, capped at 1.0.

`dominance_direction` is explicit on `ONE_SIDED_DOMINANCE` and
`RANGE_CONTAINMENT`; `none` on the other classes.

`reference_only_penalty` is 1.0 for `REFERENCE_ONLY_FV` (blocks RV handoff
as an actionable leg), 0 elsewhere.

## What belongs in graph vs RV

**Graph only** (never crosses to RV handoff):
- `WINDOW_MISMATCH_FV` (signal-to-noise too low).
- `CORRELATED_SIGNAL_ONLY` (no structural payoff tie).
- `DISCOVERY_ONLY` (already graph-only).
- Reference-only rows where BOTH legs are `REFERENCE_ONLY` (no `EXECUTABLE_LEG_OK` leg to act on).

**Graph + RV handoff** (existing pipeline via investigation packets):
- `BASIS_RISK_REVIEW` (existing `BTC_BASIS_RISK_REVIEW` is one instance; extend to other crypto sources and to politics certified/projection pairs).
- `ONE_SIDED_DOMINANCE` when both legs are `EXECUTABLE_LEG_OK` and dominance direction is unambiguous.
- `RANGE_CONTAINMENT` when both legs are `EXECUTABLE_LEG_OK`.
- `EXACT_PAYOFF_EQUIVALENCE_REVIEW` (the existing equality-style packets).
- `REFERENCE_ONLY_FV` as *fair-value context only*, carrying
  `reference_only_fv_not_executable_leg` blocker so RV cannot misuse it.

**RV-only** (graph hands off but does not own):
- Final fair-value estimation incorporating fees, depth, slippage,
  cancellation rules, region eligibility.
- Final priority ordering with portfolio context.
- Anything resembling pre-cleared candidacy or paper-trade routing.

## What should appear in ops_status

Add to `summary`:

```
fv_relationship_total
fv_basis_risk_count
fv_one_sided_dominance_count
fv_range_containment_count
fv_window_mismatch_count
fv_reference_only_count
fv_correlated_signal_only_count
fv_handoff_eligible_count   (the sum of classes that pass to RV)
fv_reference_only_penalty_blocked_count
```

Add a `top_fv_relationships` section (parallel to today's
`top_infeasibility_diagnostic`) ranked by `overall_priority_score`, capped
at 10, with the same diagnostic-only contract.

Add to `next_recommended_actions`:
- `REVIEW_TOP_FV_BASIS_RISK` when `fv_basis_risk_count > 0`.
- `REVIEW_TOP_FV_DOMINANCE` when `fv_one_sided_dominance_count + fv_range_containment_count > 0`.

## Biggest fake-edge risks (ranked)

1. **Cross-venue title match without settlement-source proof** promoted to
   `EXACT_PAYOFF_EQUIVALENCE_REVIEW`. Defense: typed extractor required;
   title similarity caps at `DISCOVERY_ONLY` (already enforced).
2. **CDNA vs Kalshi BTC promoted to exact-equivalence** because both say
   "BTC > $X". Defense: methodology fingerprint (BRTI vs U-BTC) is a hard
   `BASIS_RISK_REVIEW` boundary; never promote without manual reviewer.
3. **Polymarket deadline-hit promoted to equality with Kalshi
   point-in-time**. Defense: explicit `path_dependent_dominance` label
   blocks equality; only `ONE_SIDED_DOMINANCE` is allowed.
4. **The Odds API consensus treated as a tradable leg**. Defense:
   `executable_class=REFERENCE_ONLY` hard pre-mark; `reference_only_fv_not_executable_leg`
   blocker required on every handoff packet.
5. **Sports game-level rolled into season-futures dominance**. Defense:
   `sports_game_level_vs_futures_scope_mismatch` blocker forces
   `CORRELATED_SIGNAL_ONLY`.
6. **Election winner = officeholder confounded**. Defense: only
   `BASIS_RISK_REVIEW` unless reviewer note documents how
   death/recusal/transition are handled.
7. **AI/tech theme correlation promoted to structural relationship**.
   Defense: theme correlation caps at `CORRELATED_SIGNAL_ONLY`.
8. **Stale-quote rows ranked alongside fresh rows**. Defense:
   `quote_quality_tier` weight in `overall_priority_score`.
9. **Midpoint-only rows promoted to high priority**. Defense:
   `MIDPOINT_ONLY` weight is 0.3; bid/ask required for `EXECUTABLE_LEG_OK` rows.
10. **LLM-asserted same-event match elevating to exact-equivalence**.
    Defense: `llm_alias_advisory_only` caps at `DISCOVERY_ONLY` packet
    kind (already enforced).

## What should be built next

In ranked priority sequence:

1. **Typed `ONE_SIDED_DOMINANCE` packet kind + bridge wiring** (cross-references the prior session's prompt). Adds the `dominance_basis_risk_class` field and routes the seven labels above.
2. **`RANGE_CONTAINMENT` detector** for same-venue range-bucket vs point-in-time. Cheapest immediate FV-class win; runs entirely off the existing `range_bucket_partition` constraints.
3. **`WINDOW_MISMATCH_FV` graph-only diagnostic** so reviewers can browse them without contaminating RV handoff.
4. **`fv_relationship_total` summary section in ops_status** with the eight counters above plus `top_fv_relationships`.
5. **Crypto-source basis registry**: typed table mapping each crypto settlement source to methodology fingerprint (`BRTI`, `U-BTC`, `Binance_1m`, `Coinbase_spot`, etc.). Used by `_btc_basis_risk_context` to widen beyond BTC and to disambiguate.
6. **`REFERENCE_ONLY_FV` packet kind**: explicit packet kind alongside `BTC_BASIS_RISK_REVIEW`, so reference-only context is captured but never routed as an `EXECUTABLE_LEG_OK` leg.
7. **Politics `BASIS_RISK_REVIEW` extension**: extend `_btc_basis_risk_context` pattern to certified-vs-projection election pairs.
8. **`fake_edge_risk_class` per packet** (already partially modeled as `fake_edge_risks` list on platform radar gap rows; extend to packets).

## Safety

- All proposed outputs preserve `diagnostic_only=true`,
  `affects_evaluator_gates=false`,
  `allowed_actions=["WATCH","MANUAL_REVIEW"]`.
- No new vocabulary in this memo trips
  `PROHIBITED_REPORT_TOKENS` or
  `PROHIBITED_REPORT_PHRASES`.
- The seven relationship classes (six fair-value + one exact-equivalence)
  never grant evaluator permission. Graph's job ends at `MANUAL_REVIEW`.
- `REFERENCE_ONLY_FV` rows are excluded from being treated as
  `EXECUTABLE_LEG_OK` legs at the schema level; the
  `reference_only_fv_not_executable_leg` blocker is mandatory on every
  RV handoff packet that includes reference-only context.
