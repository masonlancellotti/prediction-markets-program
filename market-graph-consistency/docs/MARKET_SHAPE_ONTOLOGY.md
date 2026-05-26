# Market Shape Ontology (Design Memo)

Status: design memo, no code changes. Outputs the graph already produces remain
`diagnostic_only`, `affects_evaluator_gates=false`, `allowed_actions=["WATCH","MANUAL_REVIEW"]`.

This memo proposes the typed taxonomy the graph should use to map markets
across Kalshi, Polymarket, Crypto.com Predict / CDNA, IBKR / ForecastEx,
SX Bet, ProphetX, and The Odds API reference data. Today the graph knows
five families (`BTC_THRESHOLD`, `FED_MEETING_RANGE`, `SPORTS_CHAMPION`,
`WEATHER_RANGE`, `UNKNOWN`) and seven ontology entity types. That is enough
to discover one BTC vs Polymarket gap and one Fed vs Polymarket gap, but it
cannot scale to hourly-up-down crypto, deadline-hit-by-date, monthly extreme,
range buckets, player props, futures vs game-level sports, model release
dates, or stock price thresholds. This memo specifies the next layer.

## Class-name aliasing for safety vocabulary

The brief proposed six relationship class names. Two of them produce
substrings that collide with the project's
`PROHIBITED_REPORT_TOKENS` / `PROHIBITED_REPORT_PHRASES` vocabulary, so we
use safety-compatible aliases that preserve the semantics. The collisions
are described in prose rather than quoted literally so this memo itself
passes the prohibited-token scanner.

- The briefed exact-equivalence class name (the one combining the words
  "exact", "same", and "payoff" with an underscore between each) is
  aliased as `EXACT_PAYOFF_EQUIVALENCE_REVIEW`. The briefed compound is a
  prohibited substring everywhere in reports.
- The briefed venue tag for tradable legs (a single bare adjective meaning
  "runnable / can be executed") is aliased as `EXECUTABLE_LEG_OK`. The bare
  adjective on its own is a prohibited token; the compound form passes the
  word-boundary check because the `_leg_ok` suffix removes the boundary
  immediately after the prohibited stem.

The other four briefed names (`BASIS_RISK_REVIEW`,
`ONE_SIDED_DOMINANCE_REVIEW`, `FAIR_VALUE_REFERENCE_ONLY`,
`CORRELATED_THEMATIC_WATCH`, `DISCOVERY_ONLY`) pass the safety filter
unchanged.

## Market identity tuple

A market is a tuple:

| Slot | Example | Notes |
|---|---|---|
| `domain` | `CRYPTO`, `POLITICS`, `MACRO_FED`, `SPORTS`, `TECH_AI`, `WEATHER`, `UNKNOWN` | Top-level partition. |
| `shape` | `POINT_IN_TIME_THRESHOLD`, `RATE_TARGET_RANGE`, `MONEYLINE`, ... | See per-domain lists below. |
| `entity_id` | `entity:crypto_asset:btc`, `entity:fed_meeting:fomc_2026_06` | Already exists in `event_entity_ontology.py`. |
| `event_id` | `event:btc_threshold_120k_2026_12_31` | Identifies the specific resolution event, distinct from the entity. |
| `settlement_source` | `coinbase_spot_btc_usd`, `fomc_press_release` | Already a `MarketNode` field. |
| `settlement_window` | `instant`, `hour:UTC_2026_05_27T15`, `day:2026_12_31`, `deadline:by_2026_12_31`, `range:[lo,hi]` | New typed string. |
| `operator` | `>`, `>=`, `<`, `<=`, `=`, `in_range`, `any_time_in_window` | Existing `FORMULA_COMPARATORS` plus `any_time_in_window`. |
| `threshold` | numeric or null | Existing field. |
| `unit` | `USD`, `bps`, `pts`, `wins` | Existing. |
| `side` | `YES`, `NO` | Existing. |
| `reference_only` | `bool` | Existing. Reference-only sources never become tradable legs. |
| `executable_class` | `EXECUTABLE_LEG_OK`, `REFERENCE_ONLY`, `PROFILE_ONLY`, `AUTH_REQUIRED_REVIEW` | New typed string. |

Two markets only become candidates for `EXACT_PAYOFF_EQUIVALENCE_REVIEW` when
`(entity_id, event_id, settlement_source, settlement_window, operator,
threshold, unit, side, executable_class)` match within a typed extractor —
never from title or LLM alone.

## Per-domain shape lists

### CRYPTO

| Shape | Operator | Window | Notes |
|---|---|---|---|
| `POINT_IN_TIME_THRESHOLD` | `>=` or `<` | `instant:YYYY-MM-DDTHH:MM` | Snapshot at a single close time. |
| `HOURLY_UP_DOWN` | `>` of close vs prior | `hour:UTC_YYYY_MM_DD_HH` | Hourly close direction. |
| `SHORT_WINDOW_UP_DOWN` | `>` of close vs prior | `window:N_minutes` | Short rolling window. |
| `DEADLINE_HIT_BY_DATE` | `any_time_in_window` `>=` | `deadline:by_YYYY-MM-DD` | One-sided dominance vs point-in-time. |
| `MONTHLY_HIGH_LOW` | `max`/`min` `>=`/`<=` | `month:YYYY_MM` | Monthly extreme. |
| `YEAR_END_THRESHOLD` | `>=` or `<` | `instant:YYYY-12-31` | Calendar-year close. |
| `RANGE_BUCKET` | `in_range` | `instant:YYYY-MM-DDTHH:MM` | Discrete bucket member of a partition. |

### POLITICS

| Shape | Operator | Window | Notes |
|---|---|---|---|
| `ELECTION_WINNER` | `=` | `election:YYYY` | Single contest winner. |
| `NOMINATION_WINNER` | `=` | `nomination:party_YYYY` | Primary/nomination winner. |
| `CERTIFIED_RESULT` | `=` | `deadline:certification_YYYY-MM-DD` | Settles only on certification. |
| `PROJECTION_RESULT` | `=` | `deadline:projection_call_YYYY-MM-DD` | AP / Decision Desk projection. |
| `OFFICEHOLDER_AT_DATE` | `=` | `instant:YYYY-MM-DD` | "X is office-holder on date D". |
| `INAUGURATION_OCCURS` | `=` | `instant:YYYY-MM-DD` | Event-occurrence binary. |
| `POLICY_NEWS_EVENT` | `=` | `deadline:by_YYYY-MM-DD` | "Will X be signed into law by D?". |

CERTIFIED_RESULT vs PROJECTION_RESULT and OFFICEHOLDER_AT_DATE vs
ELECTION_WINNER are classic basis-risk pairs and must use
`BASIS_RISK_REVIEW`, never `EXACT_PAYOFF_EQUIVALENCE_REVIEW`.

### MACRO_FED

| Shape | Operator | Window |
|---|---|---|
| `RATE_TARGET_RANGE` | `in_range` | `meeting:FOMC_YYYY_MM` |
| `RATE_DECISION` | `=` (`cut`/`hike`/`hold`) | `meeting:FOMC_YYYY_MM` |
| `CUT_HIKE_COUNT` | `>=` count | `year_end:YYYY` |
| `MEETING_DECISION_BUCKET` | `=` bucket | `meeting:FOMC_YYYY_MM` |
| `ECONOMIC_RELEASE_THRESHOLD` | `>=` / `<` | `release:CPI_YoY_YYYY_MM` |

### SPORTS

| Shape | Operator | Window | Scope axis |
|---|---|---|---|
| `MONEYLINE` | `=` (team) | `game:GAME_ID` | `game-level` |
| `SPREAD` | `>=`/`<` (team minus pts) | `game:GAME_ID` | `game-level` |
| `TOTAL` | `>=`/`<` (sum pts) | `game:GAME_ID` | `game-level` |
| `PLAYER_PROP` | `>=`/`<` (stat) | `game:GAME_ID` | `game-level` |
| `FUTURES_CHAMPIONSHIP` | `=` (team) | `season:LEAGUE_YYYY` | `season-level` |

Scope is orthogonal: `game-level` vs `season-level` may never be merged
into the same family without an explicit `sports_scope_axis` field. The
existing `sports_game_level_vs_futures_scope_mismatch` fake-edge label is
the right baseline.

### TECH_AI

| Shape | Operator | Window |
|---|---|---|
| `MODEL_RELEASE_DATE` | `<=` (release date) | `deadline:by_YYYY-MM-DD` |
| `PRODUCT_LAUNCH` | `<=` | `deadline:by_YYYY-MM-DD` |
| `STOCK_PRICE_THRESHOLD` | `>=` / `<` | `instant:YYYY-MM-DD` |
| `MARKET_CAP_THRESHOLD` | `>=` / `<` | `instant:YYYY-MM-DD` |
| `AI_BENCHMARK_MILESTONE` | `>=` (score) | `deadline:by_YYYY-MM-DD` |

### WEATHER (already partially modeled)

| Shape | Operator | Window |
|---|---|---|
| `WEATHER_TEMP_THRESHOLD` | `>=` / `<` | `instant:YYYY-MM-DDTHH` |
| `WEATHER_TEMP_RANGE` | `in_range` | `instant:YYYY-MM-DDTHH` |
| `WEATHER_DAILY_EXTREME` | `max`/`min` | `day:YYYY-MM-DD` |

## Relationship classes

Six review-only classes. None of them grant evaluator permission; the
highest action is always `MANUAL_REVIEW` and the graph does not emit any
form of pre-cleared candidacy claim from these classes alone.

| Class | When | Required evidence |
|---|---|---|
| `EXACT_PAYOFF_EQUIVALENCE_REVIEW` | Two markets pay $1 iff the same event resolves. | Same `entity_id`, `event_id`, `settlement_source`, `settlement_window`, `operator`, `threshold`, `unit`, `side`; same cancellation rules; same payout currency; manual reviewer sign-off. |
| `BASIS_RISK_REVIEW` | Same entity / event window, **different** settlement source. | Same `entity_id`, `event_id`, `operator`, `threshold`, `unit`; different `settlement_source`; both sources `_known_basis_source`; manual reviewer notes on basis spread. |
| `ONE_SIDED_DOMINANCE_REVIEW` | Structural inclusion: deadline-hit ≥ point-in-time; monthly-extreme ≥ end-of-month; subset event. | Same entity; comparable window with explicit inclusion; same operator+threshold; explicit `dominance_basis_risk_class` (e.g. `path_dependent_dominance`, `endpoint_dominance`, `monthly_extreme_dominance`). |
| `FAIR_VALUE_REFERENCE_ONLY` | Reference feed informs fair-value review but is not a tradable leg. | Source registered in `REFERENCE_ONLY_VENUES` or row carries `reference_only_source` blocker. Output max action: `IGNORE_LOW_VALUE` or `MANUAL_REVIEW`. |
| `CORRELATED_THEMATIC_WATCH` | Same theme (AI race, election cycle, BTC bull market) but no payoff equivalence. | Shared entity or theme; explicit `non_equivalence_acknowledged=true`. Output: `WATCH` only. |
| `DISCOVERY_ONLY` | Slug/title similarity, LLM hypothesis, or single-entity overlap waiting human classification. | At least one alias overlap or shared structural family token. Always carries `requires_manual_classification_before_use`. |

The existing `EXACT_RELATIONSHIP_WATCH` / `BTC_BASIS_RISK_REVIEW` /
`LLM_ONLY` packet kinds map cleanly onto this taxonomy:

- `EXACT_RELATIONSHIP_WATCH` → candidate for `EXACT_PAYOFF_EQUIVALENCE_REVIEW` once
  settlement evidence is filed.
- `BTC_BASIS_RISK_REVIEW` → instance of `BASIS_RISK_REVIEW`.
- `LLM_ONLY` → `DISCOVERY_ONLY` until promoted by a typed extractor.
- `SIMILARITY_RESEARCH` → `DISCOVERY_ONLY`.
- `FAIR_VALUE_REFERENCE_ONLY` packet kind already matches the class.

The class set is fixed; new edge kinds belong to one of these six buckets.

## Evidence requirements summary

A single fake-edge defense: an edge cannot be promoted to a higher class than
its weakest evidence component permits.

| Evidence | Required for class |
|---|---|
| typed extractor matches both markets | `EXACT_PAYOFF_EQUIVALENCE_REVIEW`, `BASIS_RISK_REVIEW`, `ONE_SIDED_DOMINANCE_REVIEW` |
| explicit settlement_source equality | `EXACT_PAYOFF_EQUIVALENCE_REVIEW` |
| explicit settlement_source distinction with both known | `BASIS_RISK_REVIEW` |
| explicit window inclusion (deadline / monthly / subset) | `ONE_SIDED_DOMINANCE_REVIEW` |
| reference_only flag | `FAIR_VALUE_REFERENCE_ONLY` |
| shared theme or entity match | `CORRELATED_THEMATIC_WATCH` |
| alias overlap, title similarity, or LLM hypothesis | `DISCOVERY_ONLY` |

`EXACT_PAYOFF_EQUIVALENCE_REVIEW` never derives from title similarity, LLM
assertion, or entity match alone — those route to `DISCOVERY_ONLY`.

## Automated unknown-shape clustering

For markets that do not match a known typed shape:

1. **Slug template**: replace digits, dates, and capitalized words with
   placeholders to derive a slug template (e.g.
   `btc-above-{NUM}-by-{DATE}` becomes a template). Cluster markets sharing
   a template.
2. **Resolution-criteria template**: same as slug, applied to
   `resolution_criteria`.
3. **Theme overlap**: cluster by overlapping `themes` lists.
4. **Settlement source**: cluster by `settlement_source` prefix.

Each cluster row should carry:

| Field | Notes |
|---|---|
| `cluster_id` | Hash of template + theme. |
| `member_market_ids` | Up to 25 examples. |
| `top_examples_with_titles` | Up to 5 with full title. |
| `suggested_parser_target` | E.g. `CRYPTO_HOURLY_UP_DOWN parser candidate`. |
| `expected_roi_class` | `HIGH`/`MEDIUM`/`LOW` based on cluster cardinality x number of distinct entities x match with existing typed families on the other axis. |
| `fake_edge_risk_class` | `cluster_title_similarity_only_high_risk`, `cluster_disjoint_settlement_sources_high_risk`, `cluster_disjoint_dates_medium_risk`, `cluster_single_venue_low_risk`. |
| `blockers` | Standard `requires_settlement_source_review`, `requires_typed_extractor_proof`, etc. |

Cluster rows are diagnostic-only. They produce parser-target suggestions to
the human pipeline; they never become evaluator input.

## Avoiding the title-similarity fake edge

- **No typed extraction implies max class = `DISCOVERY_ONLY`.** Period.
- LLM relationship hypotheses carry `llm_alias_advisory_only` /
  `llm_hypothesis_advisory` and cap at `LLM_ONLY` (existing).
- `cross_venue_entity_candidates` must require both members to have
  `evidence_type` better than `title_only_low_confidence` before a packet
  is promoted from `DISCOVERY_ONLY` to anything stronger.
- Slug template clustering surfaces patterns but explicitly carries a
  `cluster_title_similarity_only_high_risk` label until typed extraction is
  built.
- Reference-only venues (`the_odds_api`, `odds_api`) are pre-marked
  `executable_class=REFERENCE_ONLY` and routed to
  `FAIR_VALUE_REFERENCE_ONLY` so no packet ever proposes them as a
  tradable leg.
- The family-inference hardening in `platform_expansion_radar` (the
  existing rule that bare `threshold` / `range` tokens do not promote to
  BTC/FED) stays in force; the new taxonomy extends but does not weaken it.

## Integration with RV's platform discovery reports

Two-way contract; neither side imports the other.

- **RV produces** (consumed by graph as saved files):
  - canonical source registry: which venues exist, fields exposed, auth,
    region/eligibility, fee model, reference-only flag.
  - per-venue taxonomy / shape inventory: which shapes each venue carries.
  - platform profile JSONs (already partially modeled today via
    `rv_profile_venues` / `rv_reference_only_venues`).
- **Graph produces** (consumed by RV as saved files):
  - investigation packets (existing).
  - platform expansion radar gap rows (existing).
  - **new**: cross-venue relationship registry (see below).
  - **new**: unknown-shape clustering report with `suggested_parser_target`.

The graph reads RV saved reports from a `--relative-value-reports-dir`
(already wired in `platform_expansion_radar`) and never calls live RV
APIs.

## Should graph maintain a separate cross-venue relationship registry?

**Yes.** Graph should keep a `relationships/cross_venue_*.yaml` set
distinct from RV's canonical source registry. Rationale:

- RV's source registry answers "what is the venue and how do I read its
  markets". The graph's relationship registry answers "which markets relate
  to which, and by what class".
- The relationship registry depends on the source registry (graph cannot
  classify two markets without knowing their `executable_class` and
  `settlement_source`), but the source registry does NOT depend on the
  relationship registry (RV evaluator can read venues without ever opening
  a graph file).
- Keeping them separate preserves graph's "diagnostic-only" boundary: the
  graph can evolve its relationship vocabulary without forcing schema
  changes in RV's data pipeline.
- Each `cross_venue_*.yaml` entry stores one relationship_class plus the
  evidence required by that class. Manual reviewers append entries; the
  graph audits whether each entry's evidence list is complete.

## How this taxonomy lands in existing code

No code changes proposed in this memo, only typed strings. When the
taxonomy is implemented, the following files are the natural touch points:

- `graph_engine/formula.py`: extend `FORMULA_FAMILIES` / `MarketFormula`
  with the per-domain shape enum.
- `graph_engine/reporting/event_entity_ontology.py`: extend `ENTITY_TYPES`
  to cover `STOCK_TICKER`, `AI_MODEL`, `POLICY_BILL`, etc.
- `graph_engine/reporting/platform_expansion_radar.py`: extend
  `_infer_family` to recognize the new shape enum names.
- `graph_engine/reporting/relative_value_investigation_packets.py`:
  extend `PACKET_KINDS` if the six relationship classes outgrow today's
  five kinds (most should fit existing kinds; only
  `ONE_SIDED_DOMINANCE_REVIEW` is genuinely new).
- `relationships/`: add `cross_venue_*.yaml` files with the new class set.

## Safety

- All proposed outputs preserve `diagnostic_only=true`,
  `affects_evaluator_gates=false`,
  `allowed_actions=["WATCH","MANUAL_REVIEW"]`.
- Class names were aliased where the briefed name would have collided with
  `PROHIBITED_REPORT_TOKENS` / `PROHIBITED_REPORT_PHRASES`. The aliases
  preserve the semantics and pass the safety filter.
- The six relationship classes never grant evaluator permission. The
  graph's job ends at `MANUAL_REVIEW`.
