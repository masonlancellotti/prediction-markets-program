# LLM Relationship Classifier Stub

`relative_value/llm_relationship_classifier.py` is a review-only interface for a future LLM-assisted contract relationship classifier. It does not call a model or external network service today, does not require an API key, and does not write reports by itself.

The deterministic `contract_relationship` layer remains authoritative. LLM output can only attach review metadata and can only escalate manual review. It cannot approve a candidate, cannot set `same_payoff=true`, cannot emit `EQUIVALENT`, and cannot change `PAPER_CANDIDATE`, `PAPER`, or `POSSIBLE_ARB` behavior.

## Allowed Proposal Shape

Future LLM proposals are limited to these fields:

- `proposed_relationship`
- `confidence`
- `rationale`
- `extracted_terms`
- `uncertainties`
- `manual_review_required`
- `evidence_references`

Unknown fields are rejected. Forbidden fields and tokens such as `same_payoff`, `action`, `trade_permission`, `EQUIVALENT`, `PAPER_CANDIDATE`, `PAPER`, and `POSSIBLE_ARB` are rejected.

## Audit Sidecar

The in-memory audit sidecar contains `prompt_hash`, `input_payload_hash`, `model_id`, `model_version`, `timestamp`, `raw_output`, `parsed_output`, and `validation_errors`. This is meant for future review traceability only; there is no persistence layer yet.

Semantic similarity is not settlement equivalence. A future LLM may help identify terms and uncertainty, but it cannot approve trades or override deterministic blocking reasons.
