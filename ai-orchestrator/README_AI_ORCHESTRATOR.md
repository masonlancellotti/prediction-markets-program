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

## Autonomy Ladder

Autonomy mode lives in `ai-orchestrator/state/AUTONOMY_MODE.json`.

Supported modes:

- `off`: no automated lane work.
- `gpt_one_shot_only`: conservative default. GPT can prepare one bounded handoff; Codex should not be launched automatically.
- `codex_one_task`: allow one prompt-quality-gated Codex task.
- `one_lane_supervised`: one visible lane can run under supervision after clean preflight cycles.
- `multi_lane_supervised`: full visible multi-lane supervision; do not use until several clean one-lane cycles.

Full multi-lane autonomous mode is not the default. Start with `relative_value` only, inspect the handoff files after each cycle, and stop if prompts become vague, tests fail, file scope is unclear, or Claude review is required.

## Autonomy Preflight

Run this before any live loop or Codex worker:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\test-autonomy-preflight.ps1
```

The preflight runs setup checks, task queue validation, prompt-quality validation for active lane prompts, review-gating checks, and a basic git danger scan. It reports:

- `READY_FOR_GPT_ONE_SHOT`
- `READY_FOR_CODEX_ONE_TASK`
- `BLOCKED` with reasons

Codex supervisors also call `test-next-codex-prompt.ps1` immediately before launching Codex. A prompt is rejected if it is vague, lacks task id/lane/tests/scope/success criteria/stop conditions, asks for commit/push, omits no-trading or `.env` guardrails, or appears to touch the wrong lane.

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

## Reasoning Summary Cost Control

`REASONING_SUMMARY` remains in GPT output for parser compatibility, but routine visible summaries are disabled by default:

```text
GPT_PROMPTER_REASONING_SUMMARY=0
```

Hidden model reasoning is never visible. A visible `REASONING_SUMMARY` is generated text and costs output tokens, so the default GPT prompt requires:

```text
REASONING_SUMMARY_START
UNCHANGED
REASONING_SUMMARY_END
```

Set `GPT_PROMPTER_REASONING_SUMMARY=1` only when you want a brief non-sensitive routing summary of 1-2 lines.

## Initialize

From repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\init-ai-loop.ps1
```

This creates missing `.ai_loop/` files inside existing lane folders and creates `ai-orchestrator/logs/`. It only writes lane-local `.ai_loop` files.

## One-shot GPT prompter test

One-shot GPT prompter API test for a single lane:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-gpt-prompter-once.ps1 -LaneName relative_value -TimeoutSeconds 60
```

Run this before the persistent visible loop. It builds one plain UTF-8 text context packet, starts one child Node process, calls `gpt-prompter.mjs` once with `--packet-text`, validates markers in PowerShell, writes handoff files, logs the run under `ai-orchestrator\logs\relative_value\gpt-prompter\`, and exits. It refuses to run if `ai-orchestrator\STOP.txt` exists.

No-API smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-gpt-prompter-once.ps1 -LaneName relative_value -TimeoutSeconds 30 -DebugTrace -NoApiSmoke
```

This skips Node/OpenAI, writes a plain text packet file, validates a marker-complete local output, and exits quickly. Use it when checking that the one-shot wrapper itself is not wedged.

Success looks like:

- console prints the raw output path and run log path
- exit code is `0`
- `LATEST_GPT_PROMPTER_OUTPUT.md`, `NEXT_CODEX_PROMPT.md`, and `NEXT_ACTION_PACKET.md` update after marker validation

Failure looks like:

- exit code is `1`
- `NEXT_CODEX_PROMPT.md` is not overwritten
- `FAILURE_LOG.md` and the timestamped run log explain timeout, API failure, marker failure, or write failure

Timeout behavior:

- PowerShell kills the one-shot Node child process tree after `-TimeoutSeconds` seconds. Default: `120`.
- Node aborts the OpenAI fetch with `AbortController`. Default: `120` seconds.
- Override Node's API timeout with `GPT_PROMPTER_API_TIMEOUT_SECONDS`.
- On timeout, `NEXT_CODEX_PROMPT.md` is not overwritten, the lane `FAILURE_LOG.md` is appended, and the command exits nonzero.

If a one-shot test looks stuck, inspect the latest `.log` file under `ai-orchestrator\logs\relative_value\gpt-prompter\`. The log records start/end time, timeout seconds, node command line without secrets, API start/end/timeout/failure, model used when available, marker validation, output paths, and stderr/stdout paths.

`run-gpt-prompter-lane.ps1 -Once` is deprecated and delegates to `run-gpt-prompter-once.ps1`.

## Launch Visible Loop

Safe launch procedure:

1. Run `test-autonomy-preflight.ps1`.
2. Run one GPT prompter one-shot for `relative_value`.
3. Inspect `NEXT_CODEX_PROMPT.md`, `NEXT_ACTION_PACKET.md`, command requests, and `FAILURE_LOG.md`.
4. Run `test-next-codex-prompt.ps1 -LaneName relative_value`.
5. Only then run one Codex lane worker if `AUTONOMY_MODE.json` is `codex_one_task` or higher.
6. Do not start all visible loops until several one-lane cycles are clean.

Persistent GPT prompter loop for a single lane:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-gpt-prompter-lane.ps1 -LaneName relative_value
```

Use the persistent loop only when you want it to keep polling for new Codex summaries, command results, Claude reviews, or `GPT_REVIEW_NEEDED.txt`.

After a one-shot pass, inspect:

```text
relative-value-scanner\.ai_loop\LATEST_GPT_PROMPTER_OUTPUT.md
relative-value-scanner\.ai_loop\NEXT_CODEX_PROMPT.md
relative-value-scanner\.ai_loop\NEXT_ACTION_PACKET.md
relative-value-scanner\.ai_loop\COMMANDS_SHORT_PENDING.jsonl
relative-value-scanner\.ai_loop\COMMANDS_LONG_REVIEW.md
relative-value-scanner\.ai_loop\FAILURE_LOG.md
ai-orchestrator\logs\relative_value\gpt-prompter\
```

## Launch All Visible Loops

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

Grouped Windows mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\open-visible-loop.ps1 -GroupedWindows
```

This opens three grouped Windows Terminal windows:

- `AI Loop - Codex`: relative_value, graph, and weather Codex supervisors.
- `AI Loop - Claude`: relative_value, graph, and weather Claude reviewers.
- `AI Loop - Commands`: short-command runner, command center, relative_value GPT prompter, and a monitor pane for action/long-command/failure files.

Debug launch flags:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\open-visible-loop.ps1 -GroupedWindows -CodexOnly
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\open-visible-loop.ps1 -GroupedWindows -ClaudeOnly
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\open-visible-loop.ps1 -GroupedWindows -CommandsOnly
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\open-visible-loop.ps1 -GroupedWindows -MonitorOnly
```

If `wt.exe` is unavailable, the launcher prints exact fallback PowerShell commands grouped by Codex, Claude, Commands, and Monitors. Run one-lane supervised first before overnight or multi-lane operation.

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

Codex prompts are written to timestamped prompt files under:

```text
ai-orchestrator\logs\<lane>\codex\
```

The supervisor invokes Codex with a short stdin-based command:

```powershell
codex exec --sandbox workspace-write --cd <lane-path> -
```

On this Windows setup the runner prefers the bundled Node runtime plus the installed Codex JS entrypoint, but the effective Codex CLI arguments are the same. The trailing `-` tells Codex to read the prompt from stdin. The runner uses redirected stdin/stdout/stderr, writes the full prompt text to stdin, closes stdin immediately, then waits with a finite timeout. The large prompt text is not passed as a command-line argument. Each Codex log records the prompt file path, prompt character count, argument count, argument length, process id, and whether stdin was written and closed.

If Codex stalls at command start, inspect the latest `codex_*_run*.log`. A healthy launch should show `mode: stdin_written_and_closed`, `stdin_will_be_written: true`, and `stdin_closed: true`. If Codex exits with code `2` or prints a usage error such as `unexpected argument`, inspect the latest `codex_*_run*.log` and `codex_*_prompt.md` files. The command line should stay short and should end with `--cd <lane-path> -`.

Safe Codex supervisor tests that do not launch Codex:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-codex-lane.ps1 -LaneName relative_value -PrintCodexCommandOnly
```

This validates the active prompt and prints the command shape, prompt path, prompt character count, and command argument length.

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-codex-lane.ps1 -LaneName relative_value -NoCodexSmoke
```

This validates the active prompt, writes a fake `LATEST_CODEX_SUMMARY.md`, updates heartbeat, and exits without launching Codex.

`-NoCodexSmoke` also simulates the stdin prompt path: it prepares the wrapped prompt, reports `stdin_written_and_closed`, and confirms the prompt would be written to redirected stdin and closed. It does not call Codex.

The Codex supervisor detects:

- nonzero exit
- context compaction/disconnect/rate/network signals
- summary file not updated
- empty next prompt
- timeout past `$Global:CodexTimeoutSeconds`

On failure it:

1. Appends to `FAILURE_LOG.md`.
2. Builds `RECOVERY_CONTEXT_PACKET.md`.
3. Retries once with a compact recovery prompt that tells Codex to read `.ai_loop\RECOVERY_CONTEXT_PACKET.md` and `.ai_loop\NEXT_CODEX_PROMPT.md`.
4. If retry fails, marks the lane `BLOCKED`, writes `GPT_REVIEW_NEEDED.txt`, and stops hammering Codex.

Recovery avoids embedding the recovery packet in the command line. This prevents Windows command-line length failures such as `The filename or extension is too long`.

## Claude Gating

Claude reviewer panes stay visible, but each review is a fresh stateless Opus call. Review runs only when `READY_FOR_CLAUDE_REVIEW.txt` exists and `REVIEW_POLICY.json` or the current marker indicates a high-risk review trigger, such as evaluator gates, settlement trust, fee/slippage/gas logic, graph-to-relative promotion, paper candidates, risk-sensitive changed files, or GPT explicitly requesting review.

Claude is skipped or deferred for unchanged diff hash, docs-only/status-only changes, formatting/typos, and non-risk fixture/schema-only updates. The reviewer tracks a last reviewed diff hash to avoid tight loops.

Claude output is review input. GPT converts it into a bounded task or marks the task done.

## Roadmap Replenishment

`TASK_QUEUE.json` contains tasks ready to run now. `ROADMAP_BACKLOG.json` contains candidate tasks that may become ready after ranking, prerequisites, Claude review, or Mason action.

Every GPT cycle should either produce a bounded task, request user action, request Claude review, or mark the lane blocked. It can propose 1-5 backlog additions and promote at most one backlog item into `TASK_QUEUE.json` per cycle. It must not output broad prompts like "continue previous work."

When API credentials, account connection, venue eligibility, long/manual commands, risky settlement trust, or paper-candidate inspection are needed, GPT writes a concise entry to `USER_ACTION_REQUIRED.md` instead of guessing.

## Manual Long Commands

Long/manual commands are never auto-run. GPT and Codex must write them to:

```text
<lane>\.ai_loop\COMMANDS_LONG_REVIEW.md
```

Each request uses this structure:

```text
## command-id-here
id: command-id-here
lane: relative_value
cwd: C:\absolute\lane\path
command: exact command
why_needed: why Mason should run it
blocking_task_id: task id or none
expected_output: what output matters
risk_reason: why this is manual
timeout_suggestion: seconds
status: OPEN | RUNNING | DONE | SKIPPED
```

If the command blocks the current task, GPT marks that task `WAITING_USER_COMMAND`. If another independent ready task exists in the same lane, GPT can select that next. If there is no independent task, GPT marks the lane `BLOCKED` with `BLOCKED_REASON`. If the task needs credentials, account setup, API connection, venue eligibility, or risky approval, GPT writes `USER_ACTION_REQUIRED.md` and marks the task `BLOCKED_USER`.

`NEXT_ACTION_PACKET.md` should show the command id, lane, why it is needed, and the exact command below for Mason to run. Output is appended to `COMMAND_RESULTS.md`, and GPT reads that tail on the next pass.

Foreground logged command:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-manual-command-and-log.ps1 -LaneName relative_value -CommandId command-id-here -Command "pytest tests\test_matching.py -q" -TimeoutSeconds 180
```

Background logged command:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-manual-command-and-log.ps1 -LaneName weather -CommandId command-id-here -Command "python main.py --help" -Background
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
