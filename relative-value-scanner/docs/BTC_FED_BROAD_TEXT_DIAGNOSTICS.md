# BTC/Fed Broad Text Diagnostics

`_broad_text_only` is intentionally narrow. It is a defense-in-depth allowlist for BTC/Fed exact-contract diagnostics, not a complete production title classifier.

The helper must not be used as title-similarity settlement equivalence. A broad text overlap can only support conservative diagnostics, and it does not prove that two contracts have the same payoff, source, deadline, threshold, meeting, or settlement wording.

The allowlist is not exhaustive. BTC/Fed titles such as `Bitcoin price end of year`, `BTC > 100k`, or other non-fixture-like wording may not trigger the narrow bump. That is acceptable fail-closed behavior.

Uncertain BTC/Fed title overlap remains `WATCH` or `MANUAL_REVIEW`. It must never create or promote a paper candidate.
