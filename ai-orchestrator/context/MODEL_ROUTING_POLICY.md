# Model Routing Policy

Do not include exact pricing numbers in routing decisions.

Cheap/small model:

- Command extraction.
- Command classification.
- Status normalization.
- Log clipping and tiny summaries.
- Backlog extraction from summaries and reviews.

Mid model:

- Normal Codex prompt generation.
- Routine task routing.
- Rolling lane status updates.
- Next-action packets.
- Promotion of one ready backlog item into `TASK_QUEUE.json`.

Strong model:

- Architecture planning.
- Blocked lane recovery.
- Conflicting reviews.
- Choosing between conflicting high-profit paths.
- Paper-candidate review planning.
- Evaluator, settlement, fee, slippage, gas, or trust changes.
- Any decision that might promote a candidate or weaken a safety gate.

Claude Opus:

- Strict reviewer only.
- Use sparingly for high-risk review.
- Do not use for routine prompt generation.
- Required only when a task can change evaluator gates, settlement trust, fee/slippage/gas model, graph-to-relative integration, or when a paper candidate appears.
