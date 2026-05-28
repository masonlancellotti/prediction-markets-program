# Weather Operator Runbook

Durable, exact PowerShell command set for operating the Kalshi weather-edge pipeline.
Read this before running anything weather-related. The status command
`.\python.cmd main.py weather-ops-status --last-days 7` prints a categorized command
bundle that mirrors this doc under `runbook_commands`.

## Shell ground rules

- Always work from `C:\Users\mason\Downloads\prediction-markets-program\kalshi-weather-edge`.
- In PowerShell, bare `python` resolves to the WindowsApps stub that is missing
  dependencies. Always use the repo shims:
  - `.\python.cmd ...` — runs `.\venv\Scripts\python.exe` with your args.
  - `.\pytest.cmd ...` — runs `pytest` from the repo venv.
- If a command fails with `ModuleNotFoundError: No module named 'sqlalchemy'` or
  similar, the venv is gone or the shim is bypassed. Repair with:

```powershell
.\scripts\setup-dev.ps1
.\python.cmd scripts\env_doctor.py
```

- Never edit `.env`. Never run commands that place real orders. No command in this
  runbook places orders; the only `paper-*` commands listed are research-only loops.

## Project priorities

Weather is the **lowest-priority lane** right now. Empirically:

- `recorded-sweep-attribution` reports 119 strategy variants tested; all failed.
  `late_day_high_fade/taker`, `already_hit/taker`, and `wide_spread_passive` all
  lose money in replay against the 42 high-confidence labels currently available.
- `trading-readiness` returns `NOT_READY_NO_EDGE` and `analyze-market-making`
  returns `RESEARCH_READY_NO_PAPER_EDGE_YET` (0 final paper watchlist candidates
  after hygiene removes likely-expired markets).

Relative-value (`relative-value-scanner`) and market-graph
(`market-graph-consistency`) are the **primary near-term profit lanes**. The
weather pipeline must stay a maintenance loop, not a research distraction.

## Command categories

### Read-only (safe at any time)

```powershell
.\python.cmd scripts\env_doctor.py
.\python.cmd main.py weather-ops-status --last-days 7
.\python.cmd main.py weather-data-audit
.\python.cmd main.py weather-settlement-coverage
.\python.cmd main.py weather-replay-build-coverage --last-days 7 --min-settlement-confidence 0.85
.\python.cmd main.py weather-label-expansion-plan
.\python.cmd main.py weather-recorder-health --last-hours 24
.\python.cmd main.py collector-health --last-hours 24
.\python.cmd main.py trading-readiness --last-days 7
.\python.cmd main.py recorded-sweep-attribution --last-days 7 --label-quality primary
.\python.cmd main.py source-smoke
.\python.cmd main.py project-status
```

These open SQLite read-only (`mode=ro`, `PRAGMA query_only=ON` where applicable).
They never write to the database, never hit private APIs, never print secrets.

### Safe / idempotent mutators

Each of these is upsert-keyed, so re-running is safe and produces the same row
set as the first run. None places orders.

```powershell
.\python.cmd main.py init-db
.\python.cmd main.py load-markets
.\python.cmd main.py resolve-active-weather-stations
.\python.cmd main.py build-exact-settlements --limit 200    # chunked
.\python.cmd main.py build-exact-settlements                 # full pass
.\python.cmd main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85
```

`build-exact-settlements` makes one NWS climate report + one IEM hourly
observation HTTP call per eligible contract. With ~2150 parsed contracts the
full pass can exceed 5 minutes. Each label is committed in its own transaction,
so SIGINT / timeout leaves a consistent partial set — no torn writes. Re-running
re-upserts the same `market_ticker` rows. Use `--limit 200` to make a chunked
attempt visible from progress logging.

### Analysis / sweeps (read-only effect on observability tables; safe to rerun)

```powershell
.\python.cmd main.py sweep-recorded --last-days 7 --label-quality primary
.\python.cmd main.py recorded-sweep-attribution --last-days 7 --label-quality primary
.\python.cmd main.py analyze-liquidity --last-days 7
.\python.cmd main.py analyze-market-making --last-days 7
.\python.cmd main.py validate-signals --last-days 7
.\python.cmd main.py daily-trading-research-update
```

`sweep-recorded` writes summary rows to `recorded_strategy_sweeps`. Other
analysis commands write to `reports/` and `recorded_*` summary tables. Re-running
overwrites the same observability rows.

### Continuous background collection

Start each of these in a separate PowerShell window. Each writes append-only
rows. They never place orders and they never reset state.

```powershell
.\python.cmd main.py record-orderbooks --weather-only --interval-seconds 30 --duration-hours 12
.\python.cmd main.py record-weather-observations --from-active-markets --interval-minutes 5 --duration-hours 12
.\python.cmd main.py record-weather-forecasts --from-active-markets --interval-minutes 30 --duration-hours 12
```

When `--duration-hours` elapses, restart them. To run longer, increase the
duration or wrap in a PowerShell scheduled task. There is also
`.\python.cmd main.py collect-live --hours 12 --weather-only` which runs the
combined no-trading collector; pick **one** of the explicit recorders **or**
`collect-live`, not both, or you'll double-record.

### Caution: research-only paper quoters (never place real orders, but spend time)

These do not send real orders. They simulate quote/fill behavior against the
live orderbook for research evidence. Only run them when `trading-readiness`
explicitly returns `PAPER_READY_SPECIFIC_STRATEGY` or you intend to gather paper
fill evidence on a deliberately chosen target. Do not run them speculatively.

```powershell
.\python.cmd main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60
.\python.cmd main.py paper-market-making --market-ticker <TICKER> --side BUY_YES --interval-seconds 30 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1
```

### Never run casually

```powershell
.\python.cmd main.py rebuild-clean-edge-analysis
.\python.cmd main.py reparse-contracts --weather-only --parser-version v2_range_bucket_semantics
.\python.cmd main.py rebuild-settlement-labels --weather-only
.\python.cmd main.py mark-stale-runs
.\python.cmd main.py load-history
```

These reparse/rebuild a large amount of state. They are idempotent and do not
delete the DB, but they will mark prior rows stale and rewrite derived tables.
Use only when a parser/settlement version has been intentionally bumped.

### Commands that mutate the DB and are idempotent

| Command | Tables it writes |
|---|---|
| `init-db` | schema only |
| `load-markets` | `markets`, `parsed_contracts`, `market_universe_rankings` |
| `record-orderbooks` | `orderbook_snapshots_live`, `historical_trades`, `collector_state` |
| `record-weather-observations` | `weather_observation_snapshots_live`, `collector_state` |
| `record-weather-forecasts` | `weather_forecast_snapshots_live`, `collector_state` |
| `resolve-active-weather-stations` | `active_weather_station_map` |
| `build-exact-settlements` | `settlement_labels`, `nws_daily_climate_reports` |
| `build-recorded-replay` | `recorded_orderbook_replay_snapshots` |
| `sweep-recorded` | `recorded_strategy_sweeps` |
| `validate-signals` | `backtest_trades` summary rows |
| `paper-market-making*` | `paper_market_making_quotes` |
| `analyze-market-making` | `reports/market_making_summary.json` |

### Strictly read-only commands

All `weather-*` audit/coverage/expansion/health commands, `trading-readiness`,
`analyze-liquidity`, `recorded-sweep-attribution`, `source-smoke`,
`project-status`, `weather-recorder-health`, `collector-health`, the
`paper-basket-diagnostics`, `paper-market-making-evidence`,
`paper-market-making-target-review`, `paper-market-making-drilldown`, and
`weather-replay-coverage` commands. None of these write to SQLite.

---

## Schedule

### Continuously (separate PowerShell window each)

```powershell
.\python.cmd main.py record-orderbooks --weather-only --interval-seconds 30 --duration-hours 12
.\python.cmd main.py record-weather-observations --from-active-markets --interval-minutes 5 --duration-hours 12
.\python.cmd main.py record-weather-forecasts --from-active-markets --interval-minutes 30 --duration-hours 12
```

Restart each one when its 12-hour duration elapses. Confirm freshness via
`weather-ops-status`.

### Every few hours (smoke check, ~30 seconds)

```powershell
.\python.cmd main.py weather-ops-status --last-days 7
.\python.cmd main.py weather-recorder-health --last-hours 6
.\python.cmd main.py collector-health --last-hours 6
```

Investigate immediately if any recorder shows `STALE` or `MISSING`.

### Daily (run in order)

```powershell
# 1) Verify environment and DB
.\python.cmd scripts\env_doctor.py
.\python.cmd main.py weather-data-audit
.\python.cmd main.py weather-ops-status --last-days 7

# 2) Refresh stations and exact labels (small chunks first, then full)
.\python.cmd main.py resolve-active-weather-stations
.\python.cmd main.py build-exact-settlements --limit 200
.\python.cmd main.py build-exact-settlements

# 3) Inspect what labels exist now and what is blocking replay
.\python.cmd main.py weather-settlement-coverage
.\python.cmd main.py weather-label-expansion-plan

# 4) Build replay rows for the high-confidence labels in the last week
.\python.cmd main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85
.\python.cmd main.py weather-replay-build-coverage --last-days 7 --min-settlement-confidence 0.85

# 5) Re-run sweep + attribution to see whether the new labels changed anything
.\python.cmd main.py sweep-recorded --last-days 7 --label-quality primary
.\python.cmd main.py recorded-sweep-attribution --last-days 7 --label-quality primary

# 6) Re-run liquidity and market-making (separate from weather fair-value)
.\python.cmd main.py analyze-liquidity --last-days 7
.\python.cmd main.py analyze-market-making --last-days 7
.\python.cmd main.py validate-signals --last-days 7

# 7) Final readiness verdict
.\python.cmd main.py trading-readiness --last-days 7
```

Spend ~10 minutes a day on this loop. Do not paper-trade unless step 7 returns
`PAPER_READY_SPECIFIC_STRATEGY` explicitly.

### Weekly (run in order; longer audit)

```powershell
.\pytest.cmd
.\python.cmd main.py project-status
.\python.cmd main.py source-smoke
.\python.cmd main.py weather-replay-coverage --last-days 14
.\python.cmd main.py weather-settlement-coverage
.\python.cmd main.py weather-label-expansion-plan
.\python.cmd main.py validate-settlement-labels
.\python.cmd main.py validate-settlement-sources
.\python.cmd main.py validate-orderbook-depths --last-days 7
.\python.cmd main.py audit-recorded-data
.\python.cmd main.py rank-market-universe --top 200
.\python.cmd main.py edge-report --last-days 14
```

If `pytest` fails, fix tests before running anything else.

### Before any paper-readiness decision

```powershell
.\python.cmd main.py trading-readiness --last-days 7
.\python.cmd main.py recorded-sweep-attribution --last-days 7 --label-quality primary
.\python.cmd main.py analyze-market-making --last-days 7
.\python.cmd main.py paper-market-making-evidence
.\python.cmd main.py paper-market-making-target-review
```

Only proceed if all four agree the verdict is `PAPER_READY_*` AND
`paper_watchlist_hygiene.final >= 1` AND `validate-signals` reports
`beat_rate_30m >= 0.55` with `>=10` observations on the same strategy.

### When you switch computers or sync from Google Drive

```powershell
cd "C:\Users\mason\Downloads\prediction-markets-program\kalshi-weather-edge"

.\scripts\setup-dev.ps1               # creates .venv and installs requirements.txt
.\python.cmd scripts\env_doctor.py    # confirms imports OK and shim is wired
.\python.cmd main.py weather-data-audit   # confirms DB path and freshest DB
.\python.cmd main.py weather-ops-status --last-days 7

# only start collectors if weather-data-audit shows latest_orderbook_timestamp older than 10 min
.\python.cmd main.py record-orderbooks --weather-only --interval-seconds 30 --duration-hours 12
# ...etc
```

If `weather-data-audit` reports `likely_wrong_path_detected: "true"`, **stop**
and figure out which DB the recorders should write to before touching anything.

### When a command fails

| Symptom | Action |
|---|---|
| `ModuleNotFoundError: sqlalchemy` (or similar) | `.\scripts\setup-dev.ps1` then `.\python.cmd scripts\env_doctor.py` |
| `Missing repo-local .venv Python` shim error | `.\scripts\setup-dev.ps1` |
| `weather-ops-status` shows `STALE` recorder | Restart the corresponding `record-*` command in a fresh window |
| `weather-data-audit` shows `likely_wrong_path_detected: "true"` | Stop. Inspect `freshest_db_candidate` and reconfigure `DATABASE_URL` before continuing |
| `build-exact-settlements` times out | It's fine. Run `weather-settlement-coverage` to confirm partial labels are present. Re-run with `--limit 200` chunks until the count stabilizes |
| `build-recorded-replay` reports 0 markets | Run `weather-replay-build-coverage --last-days 7 --min-settlement-confidence 0.85` to inspect the dry-run filter results |
| `trading-readiness` returns `NOT_READY_NO_EDGE` | Expected. Run `recorded-sweep-attribution` for the why |
| `analyze-market-making` shows `final=0` but `raw=1` | Expected. The hygiene removed an expired or stale market. Do not paper-trade |
| `pytest` fails | Fix the test before running anything else; do not commit |

---

## Outputs and what they mean

### Stop and investigate immediately when you see

- `weather-data-audit.summary.likely_wrong_path_detected == "true"`
- `weather-data-audit` shows `suspected_data_gap_ranges` with multi-day gaps
- `weather-ops-status.verdict == "RED_BROKEN_OR_NO_DATA"`
- Any recorder status in `weather-ops-status` is `STALE` for more than one
  consecutive check
- `validate-settlement-labels` flags a fabricated or version-mismatched label
- `validate-orderbook-depths` reports `rows_over_10000` (raw depth scaling bug)
- `audit-recorded-data` reports schema drift

### Stop coding and paper-test only if

- `trading-readiness` returns `PAPER_READY_SPECIFIC_STRATEGY` with a named strategy
  AND
- `recorded-sweep-attribution` shows that strategy with positive net edge,
  `variants_with_positive_gross_edge_but_negative_after_costs == 0`, and
  `variants_with_positive_apparent_edge_but_too_few_samples == 0`
  AND
- `analyze-market-making` shows `paper_watchlist_hygiene.final >= 1` and that
  strategy survives `paper-market-making-target-review`
  AND
- `validate-signals --last-days 7` shows `beat_rate_30m >= 0.55` with `>=10`
  observations on the same strategy
  AND
- `future_mid_validation.strength == "STRONG"` in `weather-ops-status`

Until all five are true, **continue research only**.

### Conclude weather is not the priority lane if

- After 2 weeks of `record-*` running and daily `build-exact-settlements`, the
  count of high-confidence labels has not exceeded ~150 (i.e., we are not
  averaging at least ~10 new exact labels per recorded weather day).
- `recorded-sweep-attribution` still reports `NO_PAPER_EDGE_FOUND` and the
  `wide_spread_passive` family still dominates fills with negative net.
- `analyze-market-making` `final` paper_watchlist count never exceeds 0 after
  hygiene.
- Adverse fill rate 30m stays `>= 0.15` on every market-making candidate.

In that case, weather stays a maintenance pipeline. The hours go to RV and
graph.

---

## Expected data volume

| Window | orderbook_snapshots_live | weather_observation_snapshots_live | weather_forecast_snapshots_live | historical_trades | settlement_labels (high-conf) | replay rows |
|---|---:|---:|---:|---:|---:|---:|
| Healthy 24h | +100k to +200k | +1k to +5k | +5k to +15k | +100k to +200k | +20 to +50 | +1k to +5k per high-conf label |
| Healthy 1 week | +700k to +1.4M | +7k to +35k | +35k to +100k | +700k to +1.4M | +100 to +350 | scales with labels |
| Healthy 2 weeks | +1.4M to +2.8M | +14k to +70k | +70k to +200k | +1.4M to +2.8M | +200 to +700 | scales with labels |

If actual growth is `< 50%` of the lower bound for a given window, restart the
recorders. If `< 10%`, check that you started recorders on **this** computer
after a sync from Drive and that the DB path is correct.

## Leading indicators that weather edge might be emerging

- `validate-signals --last-days 7` reports `beat_rate_30m >= 0.55` with `>= 10`
  observations for at least one named strategy.
- `recorded-sweep-attribution` shows at least one strategy family with positive
  net P&L (not just gross) after costs.
- `analyze-market-making` reports `final paper_watchlist >= 1` AND the
  candidate is a current (not expired) weather ticker, not a sports event.
- `paper-market-making-target-review` agrees with `analyze-market-making` and
  shows trade-print confirmation on the same market within the last 24 hours.
- The same strategy survives two consecutive daily sweep cycles without going
  negative.

## Leading indicators that weather is adverse-selection bait

- `wide_spread_passive` family fills > 100 but cumulative net P&L is large and
  negative (currently `-8193` cents on 274 fills).
- `analyze-market-making.adverse_fill_rate_30m >= 0.15` persists across multiple
  runs.
- Every top `analyze-market-making` candidate is flagged `[LIKELY_EXPIRED]` and
  hygiene-removed from the paper watchlist.
- `weather-label-expansion-plan` shows `deterministic_exact_labels_buildable_now`
  stuck at 0 (no new high-confidence labels possible from existing CLI reports).

## Avoiding fake edge from expired / post-day rows

The replay builder includes orderbook snapshots whose `ts` date is after the
contract's `local_date`. About 25% of current replay rows are in that bucket.
Most strategies filter via `minutes_to_close > 0` or
`is_threshold_already_hit_asof == 0`, but be aware that:

- `analyze-market-making` is the only loop that runs `_market_likely_expired`
  hygiene. Always trust its `paper_watchlist_hygiene.final` value over the
  displayed top-N (which includes `[LIKELY_EXPIRED]` rows for transparency).
- For new sports series the hygiene may not fire — file a Codex prompt to add
  the series prefix to `_EVENT_DATE_SERIES_PREFIXES`.
- Never paper-trade a `[LIKELY_EXPIRED]` market even if its score looks high.

---

## Minimum maintenance loop (so weather stays a background lane)

Once a day, run **only** this 90-second loop:

```powershell
cd "C:\Users\mason\Downloads\prediction-markets-program\kalshi-weather-edge"
.\python.cmd main.py weather-ops-status --last-days 7
.\python.cmd main.py build-exact-settlements --limit 200
.\python.cmd main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85
.\python.cmd main.py recorded-sweep-attribution --last-days 7 --label-quality primary
.\python.cmd main.py trading-readiness --last-days 7
```

Once a week, also run `.\pytest.cmd` and the weekly audit block above.

If the daily loop ever surfaces a real paper-ready candidate (see "stop coding
and paper-test only if" above), spend that day on weather and let RV/graph
catch up the next day. Otherwise keep going on RV and graph.
