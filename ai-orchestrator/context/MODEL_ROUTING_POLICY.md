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
- Routine packets remain on the mid model even if stable context mentions guardrail words such as risk, matching, same_payoff, review, or evaluator.

Strong model:

- Architecture planning.
- Blocked lane recovery.
- Conflicting reviews.
- Choosing between conflicting high-profit paths.
- Paper-candidate review planning.
- Repeated failure count greater than one.
- Explicit Claude/Codex disagreement.
- `paper_candidate_found` or equivalent paper-candidate appearance.
- Evaluator gate changes.
- Settlement trust changes.
- Fee, slippage, or gas model changes.
- Graph-to-relative integration.
- Live execution design.
- Any decision that might promote a candidate or weaken a safety gate.

Strong model routing should require one of those explicit high-risk or blocked-lane triggers. Generic stable-context words like risk, matching, same_payoff, review, evaluator, or settlement are not enough by themselves.

`gpt-5.5` is rare. Do not use it for ordinary task selection, normal prompt generation, routine status updates, command extraction, or log summaries.

Claude Opus:

- Strict reviewer only.
- Use sparingly for high-risk review.
- Do not use for routine prompt generation.
- Required only when a task can change evaluator gates, settlement trust, fee/slippage/gas model, graph-to-relative integration, or when a paper candidate appears.
- Claude is sparse and review-only; GPT converts Claude feedback into one bounded task rather than letting Claude choose broad roadmap direction.
