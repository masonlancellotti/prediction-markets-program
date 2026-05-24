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
