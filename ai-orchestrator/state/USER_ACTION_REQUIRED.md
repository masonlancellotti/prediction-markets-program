# User Action Required

Use this file when automation cannot safely proceed without Mason. The prompter should pause the task instead of guessing.

Format:

```text
## ID
Lane:
Request:
Why needed:
Blocked task:
Exact user action:
Safe to continue without it: yes/no
```

## venue-sxbet-access
Lane: orchestrator
Request: Confirm whether SX Bet should remain research-only or become an approved future integration target.
Why needed: Venue expansion may require eligibility/account/API decisions that automation must not infer.
Blocked task: venue-sxbet-research-only-inventory
Exact user action: Decide whether to keep SX Bet as research-only for now.
Safe to continue without it: yes

## 2026-05-24T08:11:18Z GPT user action request
## rv-live-readonly-scope-hygiene
Lane: relative_value
Request: Resolve or explicitly isolate the existing working-tree changes outside the selected task scope before running Codex on `rv-live-readonly-universe-snapshots`.
Why needed: The selected task allows relative-value lane files only, but current status includes modified/untracked `../ai-orchestrator/**` and repo-root files. The lane must fail closed rather than mixing unrelated changes into relative-value work.
Blocked task: rv-live-readonly-universe-snapshots
Exact user action: Decide whether those unrelated files should be committed, reverted, stashed, or handled in a separate orchestrator task before relative-value Codex work starts.
Safe to continue without it: no

## orchestrator-api-config
Lane: orchestrator
Request: Fix GPT prompter API setup through the approved local secret/config mechanism.
Why needed: Recent failures show missing, placeholder, or invalid OpenAI API configuration, which prevents real GPT prompter runs. Do not paste secrets into chat or repo files.
Blocked task: orchestrator-gpt-api-config-preflight
Exact user action: Configure a valid API key locally using the approved mechanism, or continue using no-API smoke mode until ready.
Safe to continue without it: yes for no-API smoke tests, no for real GPT prompter runs


## 2026-05-24T08:52:59Z GPT user action request
## rv-codex-runner-invocation-blocker
Lane: relative_value
Request: Repair or verify the local Codex runner invocation path before relaunching `rv-live-readonly-universe-snapshots`.
Why needed: The last Codex attempt failed before task execution with `unexpected argument 'status'`, and retry failed with a filename/extension-too-long error. This means the task did not run and the lane should not keep retrying blindly.
Blocked task: rv-live-readonly-universe-snapshots
Exact user action: Resolve or isolate the local orchestrator/Codex wrapper issue and out-of-scope `../ai-orchestrator/**` working-tree changes through the approved local workflow. Do not paste command output manually; let results return through `.ai_loop` logs/state files.
Safe to continue without it: no


## 2026-05-24T09:14:18Z GPT user action request
## rv-scope-hygiene-before-next-task
Lane: relative_value
Request: Resolve or explicitly isolate out-of-scope orchestrator/root working-tree changes before starting the next relative-value Codex task.
Why needed: The completed `rv-live-readonly-universe-snapshots` task was relative-value scoped, but current git status still includes `../ai-orchestrator/**` modifications and orchestration artifacts. The lane must fail closed rather than mix unrelated changes into the next feature task.
Blocked task: next relative-value task selection
Exact user action: Commit, stash, revert, or otherwise isolate the out-of-scope changes through the approved local workflow, then rerun the GPT prompter. Do not paste command output manually.
Safe to continue without it: no

