# AI Orchestrator Context System

This folder holds durable context and state for an OpenAI API orchestrator that coordinates Codex worker windows and Claude reviewer windows.

## Context Loading

Always-sent stable context:

- `context/PROJECT_CHARTER.md`
- `context/GLOBAL_GUARDRAILS.md`
- `context/COMMAND_POLICY.md`
- `context/PROMPTER_POLICY.md`
- `context/OUTPUT_SCHEMAS.md`
- `context/MODEL_ROUTING_POLICY.md`
- each lane's `.ai_loop/LANE_CONTEXT.md`

Dynamic state sent when relevant:

- `state/PROGRAM_STATUS.md`
- `state/ACTIVE_GOALS.md`
- `state/NEXT_STEPS.md`
- `state/DECISION_LOG.md`
- `state/TASK_QUEUE.json`
- `state/REVIEW_POLICY.json`
- `state/ROADMAP_BACKLOG.json`
- `state/FEATURE_IDEAS.md`
- `state/USER_ACTION_REQUIRED.md`
- `state/BLOCKER_ANALYSIS.md`
- `state/VENUE_EXPANSION_PLAN.md`
- each lane's `.ai_loop/LANE_STATUS.md`
- each lane's `.ai_loop/NEXT_CODEX_PROMPT.md`
- each lane's latest summary/review/action packet

Tailed files:

- `.ai_loop/COMMAND_RESULTS.md`
- `.ai_loop/FAILURE_LOG.md`
- recent worker/reviewer logs if the supervisor creates external logs later

No file is allowed to become a giant log. Long-running output should be summarized, tailed, or moved to ignored logs.

## Recovery Context

`.ai_loop/RECOVERY_CONTEXT_PACKET.md` is generated only after worker failure, context compaction, timeout, or other interruption. It should include compact stable context, current lane state, recent summaries/reviews, command result tails, failure log tail, and a small git/file-change summary if available.

## Roles

Codex worker windows:

- Do one small bounded task.
- Update lane handoff files.
- Request commands through command files.
- Do not trade, place orders, alter auth, read secrets, or edit `.env`.

Claude reviewer windows:

- Strict reviewer only.
- Used sparingly for high-risk review.
- Required for paper candidates, evaluator gates, settlement trust, fee/slippage/gas model changes, graph-to-relative integration, or live-execution design.

GPT prompter:

- Owns the roadmap and task queue.
- Builds compact next prompts.
- Selects exactly one bounded ready task per lane.
- Adds concrete backlog ideas from fresh summaries, reviews, reports, blockers, and user-needed venue/API setup.
- Pauses into `USER_ACTION_REQUIRED.md` when Mason approval, credentials, account connection, long commands, or paper-candidate inspection is needed.
- Routes commands into safe-short or long/manual files.
- Keeps outputs structured with exact markers from `OUTPUT_SCHEMAS.md`.

## Logging Discipline

Keep durable context small. Status files should be concise. Command results and failure logs should be tailed by supervisors. Huge logs should live under ignored log paths, not in context/state files.
