"""Regression guards for ``docs/MARKET_SHAPE_ONTOLOGY.md``.

The memo proposes the typed market-shape taxonomy and the six review-only
relationship classes that future code should use to map markets across
Kalshi, Polymarket, Crypto.com Predict / CDNA, IBKR / ForecastEx, SX Bet,
ProphetX, and The Odds API reference data. It is design-only — no code
changes — but it must stay safety-clean (no prohibited tokens, no
evaluator-permission vocabulary) so reviewers can quote it directly in
PRs and Slack without risking the project's diagnostic-only contract.
"""

from __future__ import annotations

from pathlib import Path

from graph_engine.reporting.safety import find_prohibited_rendered_text


MEMO_PATH = Path(__file__).resolve().parents[1] / "docs" / "MARKET_SHAPE_ONTOLOGY.md"
RELATIONSHIP_CLASSES = (
    "EXACT_PAYOFF_EQUIVALENCE_REVIEW",
    "BASIS_RISK_REVIEW",
    "ONE_SIDED_DOMINANCE_REVIEW",
    "FAIR_VALUE_REFERENCE_ONLY",
    "CORRELATED_THEMATIC_WATCH",
    "DISCOVERY_ONLY",
)
DOMAIN_LABELS = (
    "CRYPTO",
    "POLITICS",
    "MACRO_FED",
    "SPORTS",
    "TECH_AI",
    "WEATHER",
)


def test_memo_exists() -> None:
    assert MEMO_PATH.exists(), "MARKET_SHAPE_ONTOLOGY.md memo must remain in docs/"


def test_memo_carries_no_prohibited_tokens() -> None:
    # The memo names the briefed class names in prose to explain the safety
    # aliasing. If a future editor pastes the briefed literals back in, the
    # safety scanner will flag them here so the contract stays enforceable.
    text = MEMO_PATH.read_text(encoding="utf-8")
    findings = find_prohibited_rendered_text(text)
    assert findings == [], f"prohibited tokens leaked into memo: {findings}"


def test_memo_lists_all_six_relationship_classes() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    missing = [name for name in RELATIONSHIP_CLASSES if name not in text]
    assert not missing, f"relationship classes missing from memo: {missing}"


def test_memo_names_every_domain() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    missing = [name for name in DOMAIN_LABELS if name not in text]
    assert not missing, f"domain labels missing from memo: {missing}"


def test_memo_preserves_diagnostic_only_boundary() -> None:
    text = MEMO_PATH.read_text(encoding="utf-8")
    assert "diagnostic_only" in text
    assert "MANUAL_REVIEW" in text
    assert "affects_evaluator_gates=false" in text
