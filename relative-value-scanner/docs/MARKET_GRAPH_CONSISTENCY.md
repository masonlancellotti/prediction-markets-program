# Market Graph Consistency Diagnostics

## Purpose

`market-graph-diagnostics` is a fixture-backed relationship report for deterministic market graph edges. It is limited to relationship diagnostics and human review.

It does not call live APIs, mutate matcher or evaluator output, connect accounts, sign requests, route orders, or grant readiness.

## Command

```powershell
python scan.py market-graph-diagnostics --json-output reports\market_graph_consistency_diagnostics.json --markdown-output reports\market_graph_consistency_diagnostics.md
```

## Inputs

By default the command uses built-in static fixtures. An optional local JSON fixture can be passed with `--fixture`; it must be either a list of market objects or an object with a `markets` list.

The output always records:

- `data_source_mode=STATIC_FIXTURE`
- `live_fetch_attempted=false`
- `diagnostic_only=true`

## Edge Schema

Each edge includes:

- `source_market_id`
- `target_market_id`
- `relation_type`
- `direction`
- `hard_bound_type`
- `required_conditions`
- `blockers`
- `confidence`
- `source`
- `diagnostic_only=true`
- `action` limited to `WATCH` or `MANUAL_REVIEW`

Supported relationship labels are:

- `EXACT_SAME_PAYOFF`
- `COMPLEMENT`
- `SUBSET`
- `SUPERSET`
- `MUTUALLY_EXCLUSIVE`
- `EXHAUSTIVE_GROUP`
- `OVERLAP_NOT_EQUIVALENT`
- `CORRELATED_ONLY`
- `UNRELATED`
- `MANUAL_REVIEW`

## Deterministic Examples

- World Series winner implies ALCS/NLCS or league/conference winner only in the subset direction; the reverse implication is not valid.
- BTC above a higher threshold by the same date/source implies BTC above a lower threshold by the same date/source.
- Candidates in the same single-winner election are mutually exclusive.
- Exhaustive group sum-to-one diagnostics require an explicit complete-group fixture flag.
- Cleveland Browns and Cleveland Guardians are unrelated despite city token overlap.
- OpenAI IPO timing and OpenAI/Anthropic IPO ordering overlap but are not equivalent.

## Non-Goals

The graph report does not:

- call live APIs
- scrape, automate a browser, or use proxy/VPN flows
- authenticate or access accounts, balances, positions, wallets, keys, or sessions
- place, cancel, sign, or route orders
- lower matching thresholds or relationship gates
- integrate into evaluator promotion paths
- emit promoted readiness labels
