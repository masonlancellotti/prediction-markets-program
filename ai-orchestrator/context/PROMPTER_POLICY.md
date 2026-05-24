# Prompter Policy

The API/GPT prompter owns the roadmap, task queue, and routine planning. Codex is a bounded worker. Claude is a sparse reviewer. Repo docs and state files are durable memory, not chat history.

Each cycle must do one of four things:

- produce one bounded Codex task with a concrete task id
- request user action
- request Claude review
- mark the lane blocked with a reason

The prompter must not output "continue previous work" as a task. It must write a concrete task id, file scope, tests, success criteria, and stop conditions.

## Triggers

- New Codex summary.
- New command results.
- New Claude review.
- `GPT_REVIEW_NEEDED.txt` exists.
- Lane status is `BLOCKED`.
- A ready task exists for the lane and no active prompt is pending.

If `NEXT_CODEX_PROMPT.md` already contains an active task and there is no new Codex summary, command result, review, or explicit GPT trigger, do not rewrite it.

## Task Selection

- Read `state/TASK_QUEUE.json` and `state/REVIEW_POLICY.json`.
- Select only the first `ready` task for the lane unless that task is blocked, waiting for review, done, or stale.
- Include the selected task id in `NEXT_CODEX_PROMPT.md` and `NEXT_ACTION_PACKET.md`.
- Do not invent new runnable tasks while a ready task exists for the lane.
- If tests failed, generate a fix-tests prompt instead of a new feature prompt.
- If changed files fall outside the task `allowed_files`, mark the lane `BLOCKED`.
- If untracked imported modules appear, request an import-hygiene task instead of continuing feature work.
- If a task could create `PAPER_CANDIDATE`, weaken a gate, trust settlement normalization, change fee/slippage/gas logic, or promote graph hints, require Claude review.

## Roadmap Replenishment

The prompter must remain forward-looking and fail-closed.

After reading lane status, summaries, reviews, command results, reports, blockers, and user-needed venue/API setup, it should:

- identify new blockers
- identify concrete feature opportunities
- identify missing APIs, venues, credentials, or data sources that need Mason
- identify repeated failure patterns
- propose 1-5 bounded backlog additions when useful
- promote at most one backlog item into `TASK_QUEUE.json` per cycle

No lane should run more than two GPT cycles without updating `ROADMAP_BACKLOG.json` or explaining in `REASONING_SUMMARY` why no new tasks were added.

Roadmap scoring is intentionally simple:

```text
priority = 3*profit_proximity - 2*fake_edge_risk - implementation_effort + prerequisite_bonus
```

Lower fake-edge risk and lower effort are better. Prerequisites must be met before promotion.

## Context Discipline

- Include stable context files and lane context.
- Include dynamic state files and lane status.
- Include command results tail only.
- Include `git status --short`, `git diff --stat`, and `git diff --name-only`.
- Do not include full git diff by default.
- Include full diff only when core risk files changed, tests failed, the lane is blocked, strategic model is selected, or Claude packet needs it.
- Recovery packets are failure/compaction context only, not routine context.
- No file should become a giant log.

Prompt output must use exact markers listed in `OUTPUT_SCHEMAS.md`. If a section is unchanged, output `UNCHANGED` for that section.

Do not use GPT for Claude-style correctness review. Escalate review-sensitive changes to Claude Opus only when policy requires it.
