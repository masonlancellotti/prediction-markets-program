"""Regression guards for ``docs/FAIR_VALUE_RELATIONSHIPS.md``.

The memo defines six review-only fair-value relationship classes and the
typed dominance labels the graph should emit. It is design-only -- no code
changes -- but it must stay safety-clean (no prohibited tokens, no
evaluator-permission vocabulary) so reviewers can quote it directly in
PRs and Slack without risking the project's diagnostic-only contract.
"""

from __future__ import annotations

from pathlib import Path

from graph_engine.reporting.safety import find_prohibited_rendered_text


MEMO_PATH = Path(__file__).resolve().parents[1] / "docs" / "FAIR_VALUE_RELATIONSHIPS.md"
FAIR_VALUE_CLASSES = (
    "BASIS_RISK_REVIEW",
    "ONE_SIDED_DOMINANCE",
    "RANGE_CONTAINMENT",
    "WINDOW_MISMATCH_FV",
    "REFERENCE_ONLY_FV",
    "CORRELATED_SIGNAL_ONLY",
)
DOMINANCE_LABELS = (
    "endpoint_dominance",
    "interior_dominance",
    "monthly_extreme_dominance",
    "path_dependent_dominance",
    "range_subset_dominance",
    "nested_subset_dominance",
)


def test_memo_exists() -> None:
    assert MEMO_PATH.exists(), "FAIR_VALUE_RELATIONSHIPS.md memo must remain in docs/"


def test_memo_carries_no_prohibited_tokens() -> None:
    # The memo references existing safety vocabulary in negative contexts
    # (e.g. "never crosses to RV handoff"). If a future editor pastes a
    # bare prohibited token, the safety scanner will flag it here so the
    # contract stays enforceable.
    text = MEMO_PATH.read_text(encoding="utf-8")
    findings = find_prohibited_rendered_text(text)
    assert findings == [], f"prohibited tokens leaked into memo: {findings}"


def test_memo_lists_all_six_fair_value_classes() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    missing = [name for name in FAIR_VALUE_CLASSES if name not in text]
    assert not missing, f"fair-value classes missing from memo: {missing}"


def test_memo_lists_every_dominance_label() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    missing = [name for name in DOMINANCE_LABELS if name not in text]
    assert not missing, f"dominance labels missing from memo: {missing}"


def test_memo_preserves_diagnostic_only_boundary() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    assert "diagnostic_only" in text
    assert "MANUAL_REVIEW" in text
    assert "affects_evaluator_gates=false" in text


def test_memo_documents_reference_only_blocker() -> None:
    # The reference-only handoff path must always carry the
    # "not an executable leg" blocker on every RV handoff packet.
    text = MEMO_PATH.read_text(encoding="utf-8")
    assert "reference_only_fv_not_executable_leg" in text


def test_memo_documents_top_fv_relationships_section() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    assert "top_fv_relationships" in text
    assert "fv_handoff_eligible_count" in text
