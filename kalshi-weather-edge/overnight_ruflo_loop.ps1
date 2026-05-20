$ErrorActionPreference = "Continue"

$start = Get-Date
$hoursToRun = 8
$maxCycles = 20
$logDir = "overnight_ruflo_logs_" + (Get-Date -Format "yyyyMMdd_HHmmss")

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

for ($i = 1; $i -le $maxCycles; $i++) {
    $elapsed = ((Get-Date) - $start).TotalHours
    if ($elapsed -ge $hoursToRun) {
        "Stopping: time budget reached after $elapsed hours." | Tee-Object -FilePath "$logDir\stop_reason.txt"
        break
    }

    $cycleLog = "$logDir\cycle_$i.log"

    $prompt = @"
Continue the Kalshi research repo work. This is autonomous cycle $i.

Do not restart from scratch. Use existing project files, overnight_autonomous_report.md if it exists, and current git state.

Goal:
Move the project closer to real, tradable, risk-controlled prediction-market edge as quickly as possible, without fake P&L, stale-run contamination, midpoint-fill fantasy, weak settlement evidence, or overfit strategy results.

Current known context:
- trading-readiness has been NOT_READY_NO_EDGE.
- tests recently passed.
- analyze-liquidity may overstate paper readiness when fills are zero.
- analyze-market-making found trade-evidence fills and one paper-watchlist candidate.
- next useful command has been:
  python main.py backtest-market-making --last-days 1 --max-markets 100 --no-export

Hard constraints:
- Do not enable live trading.
- Do not call Kalshi order endpoints.
- Do not edit .env.
- Do not delete, rewrite, vacuum, reset, or replace kalshi_weather_edge.db, kalshi_weather_edge.db-wal, or kalshi_weather_edge.db-shm.
- Do not run git reset --hard.
- Do not run git clean -fdx.
- Do not commit.
- Do not expand to Polymarket/Kraken/Robinhood tonight.
- Do not claim profitability from stale runs.
- Do not use midpoint fills or touched-only fills as proof.
- Do not ignore fees.
- Do not make broad rewrites.

Cycle instructions:
1. Run git status --short.
2. Read overnight_autonomous_report.md if it exists.
3. Pick one highest-ROI safe improvement.
4. Implement it.
5. Add or update tests.
6. Run:
   python -m pytest -q
   python main.py project-status
   python main.py trading-readiness --last-days 7
7. If tests fail, fix them before finishing.
8. Append a report to overnight_autonomous_report.md.

Priority order:
1. Fix misleading liquidity/market-making readiness wording when evidence has zero/weak fills.
2. Improve market-making diagnostics for stale, expired, zero-fill, weak-fill, or low-evidence candidates.
3. Improve stale-run/report/dashboard exclusion clarity.
4. Improve recorder/collector health diagnostics.
5. Improve paper target selection/reporting.
6. Improve docs only if it prevents future agent confusion.

At the end of this cycle:
- write exactly what changed
- files changed
- commands run
- test result
- readiness result
- next exact command
- whether another cycle is useful

Then stop. The outer PowerShell loop will start the next cycle.
"@

    "===== START CYCLE $i at $(Get-Date) =====" | Tee-Object -FilePath $cycleLog

    claude -p $prompt --continue --dangerously-skip-permissions --model sonnet --output-format text *>> $cycleLog

    "===== END CYCLE $i at $(Get-Date) =====" | Tee-Object -FilePath $cycleLog -Append

    git status --short > "$logDir\git_status_after_cycle_$i.txt"
    git diff --stat > "$logDir\git_diff_stat_after_cycle_$i.txt"
    git diff > "$logDir\git_diff_after_cycle_$i.diff"
}
