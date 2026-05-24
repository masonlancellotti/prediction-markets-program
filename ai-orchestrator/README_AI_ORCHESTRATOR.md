# AI Orchestrator Supervisor

This is a local, visible PowerShell supervisor for coordinating three project lanes through file-based handoff. It keeps stable context separate from dynamic lane state, uses safe command request files instead of manual output pasting, and includes watchdog recovery for Codex failures.

It does not start automatically.

## Lanes

Edit `ai-orchestrator/lanes.ps1` if folders move.

- `weather` -> `kalshi-weather-edge`
- `relative_value` -> `relative-value-scanner`
- `graph` -> `market-graph-consistency`

The same file also controls supervisor settings:

- `$Global:ReviewEvery = 4`
- `$Global:GptCheapModel = "gpt-5.4-nano"`
- `$Global:GptDefaultModel = "gpt-5.4-mini"`
- `$Global:GptStrategicModel = "gpt-5.5"`
- `$Global:ClaudeModel = "opus"`
- timeout, retry, and polling settings

## Context Model

Stable context lives in `ai-orchestrator/context/`:

- `PROJECT_CHARTER.md`
- `GLOBAL_GUARDRAILS.md`
- `COMMAND_POLICY.md`
- `PROMPTER_POLICY.md`
- `OUTPUT_SCHEMAS.md`
- `MODEL_ROUTING_POLICY.md`

Dynamic program state lives in `ai-orchestrator/state/`:

- `PROGRAM_STATUS.md`
- `ACTIVE_GOALS.md`
- `NEXT_STEPS.md`
- `DECISION_LOG.md`
- `TASK_QUEUE.json`
- `REVIEW_POLICY.json`
- `ROADMAP_BACKLOG.json`
- `FEATURE_IDEAS.md`
- `USER_ACTION_REQUIRED.md`
- `BLOCKER_ANALYSIS.md`
- `VENUE_EXPANSION_PLAN.md`

Each lane gets `.ai_loop/` handoff files when initialized, including lane status, next Codex prompt, latest summaries/reviews, command request/result files, recovery packet, failure log, heartbeat, and run counters.

The API/GPT prompter owns roadmap and task-queue updates. Codex receives exactly one bounded task from the selected task id. Claude is a sparse reviewer and does not own the roadmap.

## Model Routing

GPT routing is dynamic:

- `gpt-5.4-nano`: command extraction/classification and tiny summaries.
- `gpt-5.4-mini`: normal prompt generation and rolling status updates.
- `gpt-5.5`: blocked lanes, repeated failures, cross-lane strategy changes, architecture planning, Claude/Codex disagreement, or risky planning.

Claude always uses stateless Opus calls:

```powershell
claude -p --model opus
```

Claude is for review gates, not routine prompt generation.

## Initialize

From repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\init-ai-loop.ps1
```

This creates missing `.ai_loop/` files inside existing lane folders and creates `ai-orchestrator/logs/`. It only writes lane-local `.ai_loop` files.

## Launch Visible Loop

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\open-visible-loop.ps1
```

This runs init, clears `STOP.txt`, then opens visible Windows Terminal panes:

- 3 Codex supervisors
- 3 GPT prompters
- 3 Claude reviewers
- short-command runner
- command center

If `wt.exe` is unavailable, it prints manual commands for separate PowerShell windows.

## Stop

```powershell
New-Item ".\ai-orchestrator\STOP.txt" -ItemType File -Force
```

Each loop polls this file and exits cleanly.

## Command Handoff

Agents should not ask Mason to paste command outputs. They write requests to files.

Safe short commands go to:

```text
<lane>\.ai_loop\COMMANDS_SHORT_PENDING.jsonl
```

Required JSONL shape:

```json
{"id":"unique-stable-id","classification":"SAFE_SHORT_AUTO","cwd":"C:\\absolute\\lane\\path","command":"git status --short","reason":"why needed","expected_output":"what success looks like","timeout_seconds":120}
```

The short-command runner writes output or rejection to:

```text
<lane>\.ai_loop\COMMAND_RESULTS.md
```

It appends completed IDs to:

```text
<lane>\.ai_loop\COMMANDS_DONE.jsonl
```

GPT then reads `COMMAND_RESULTS.md` tail automatically. No copy/paste loop.

Long/manual/risky commands go to:

```text
<lane>\.ai_loop\COMMANDS_LONG_REVIEW.md
```

## Codex Recovery

The Codex supervisor detects:

- nonzero exit
- context compaction/disconnect/rate/network signals
- summary file not updated
- empty next prompt
- timeout past `$Global:CodexTimeoutSeconds`

On failure it:

1. Appends to `FAILURE_LOG.md`.
2. Builds `RECOVERY_CONTEXT_PACKET.md`.
3. Retries once with the same task plus recovery context.
4. If retry fails, marks the lane `BLOCKED`, writes `GPT_REVIEW_NEEDED.txt`, and stops hammering Codex.

## Claude Gating

Claude reviewer panes stay visible, but each review is a fresh stateless Opus call. Review runs only when `READY_FOR_CLAUDE_REVIEW.txt` exists and `REVIEW_POLICY.json` or the current marker indicates a high-risk review trigger, such as evaluator gates, settlement trust, fee/slippage/gas logic, graph-to-relative promotion, paper candidates, risk-sensitive changed files, or GPT explicitly requesting review.

Claude is skipped or deferred for unchanged diff hash, docs-only/status-only changes, formatting/typos, and non-risk fixture/schema-only updates. The reviewer tracks a last reviewed diff hash to avoid tight loops.

Claude output is review input. GPT converts it into a bounded task or marks the task done.

## Roadmap Replenishment

`TASK_QUEUE.json` contains tasks ready to run now. `ROADMAP_BACKLOG.json` contains candidate tasks that may become ready after ranking, prerequisites, Claude review, or Mason action.

Every GPT cycle should either produce a bounded task, request user action, request Claude review, or mark the lane blocked. It can propose 1-5 backlog additions and promote at most one backlog item into `TASK_QUEUE.json` per cycle. It must not output broad prompts like "continue previous work."

When API credentials, account connection, venue eligibility, long/manual commands, risky settlement trust, or paper-candidate inspection are needed, GPT writes a concise entry to `USER_ACTION_REQUIRED.md` instead of guessing.

## Manual Long Commands

Foreground logged command:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-manual-command-and-log.ps1 -LaneName relative_value -Command "pytest tests\test_matching.py -q" -TimeoutSeconds 180
```

Background logged command:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-manual-command-and-log.ps1 -LaneName weather -Command "python main.py --help" -Background
```

Background process info is written to `<lane>\.ai_loop\BACKGROUND_JOBS.md`.

## Logs

Logs live under:

```text
ai-orchestrator\logs\
```

Per lane:

```text
ai-orchestrator\logs\<lane>\codex\
ai-orchestrator\logs\<lane>\gpt-prompter\
ai-orchestrator\logs\<lane>\claude\
ai-orchestrator\logs\<lane>\commands\
ai-orchestrator\logs\<lane>\recovery\
```

## One-Lane Dry Run

Safe no-API dry run:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-gpt-prompter-dry-run.ps1 -LaneName relative_value
```

This initializes lane handoff files and writes a fake bounded GPT handoff for `relative_value` without calling OpenAI, Claude, or Codex. Inspect `NEXT_CODEX_PROMPT.md`, `NEXT_ACTION_PACKET.md`, `COMMANDS_SHORT_PENDING.jsonl`, and `COMMANDS_LONG_REVIEW.md`.

API dry run, only after you intentionally set `OPENAI_API_KEY`, can be done by starting one GPT prompter pane for `relative_value`; do not start Codex or Claude until the handoff looks right.

## Do Not Automate

Do not automate live trading, order submission, auth/account/private-key/signing/wallet logic, cloud deploys, git push, destructive filesystem actions, installs, or long/risky commands.

Prediction-market guardrails:

- Relative-value dislocations are not guaranteed edge.
- Do not confuse detected mismatch with tradable/profitable opportunity.
- No midpoint-fill assumptions.
- No profit claims from stale quotes.
- Sportsbook/reference odds are reference-only unless explicitly executable through approved APIs.
- No title similarity as settlement equivalence.
- Do not ignore fees, slippage, spread, posted size, quote age/freshness, settlement wording, deadline/timezone, or liquidity.
- Uncertain outputs stay `WATCH` or `MANUAL_REVIEW`.
- Graph hints are diagnostic-only and must not become paper candidates.
- Weather edge requires external observations/forecasts/settlement labels and conservative validation.
- No live trading/order/auth/account/private-key logic.
- If a task could create `PAPER_CANDIDATE`, weaken a gate, trust new settlement normalization, alter fee/slippage logic, or promote graph hints into executable signals, require Claude Opus review.
