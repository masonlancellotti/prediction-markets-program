"""Central report-safety vocabulary and detection helpers.

The graph engine enforces two safety contracts for every generated report:

1. **Diagnostic-only:** outputs may never claim executable trade size, fills,
   profit, evaluator readiness, equality-of-payoff, or paper-trade candidacy.
2. **Substring-tight:** compound prohibited names such as ``paper_candidate``
   or ``exact_same_payoff`` are rejected even when embedded inside larger
   identifiers (``graph_hint_is_paper_candidate``, ``is_exact_same_payoff_v2``,
   etc.) so that future code cannot quietly reintroduce the unsafe vocabulary
   by nesting a token inside another word.

Two vocabularies are tracked:

``PROHIBITED_REPORT_TOKENS``
    Single-word tokens detected with word-bounded match
    (``\\btoken\\b``).  These are short, ambiguous words (``trade``,
    ``order``, ``paper``...) that need a real word boundary to avoid false
    positives on legitimate compounds (e.g. ``recorder`` should not trip
    ``order``).  The boundary check still catches the bare word in any
    serialised key or value.

``PROHIBITED_REPORT_PHRASES``
    Compound underscored or hyphenated phrases detected with case-folded
    substring match.  These are always unsafe regardless of where they
    appear, so a substring match is the appropriate (and tighter) check.

The same two vocabularies are applied to both keys and string values in
:func:`find_prohibited_report_tokens`.
"""

from __future__ import annotations

import re
from typing import Any


PROHIBITED_REPORT_TOKENS = {
    "arb",
    "buy",
    "dollars",
    "edge_bps",
    "executable",
    "fill",
    "order",
    "paper",
    "pnl",
    "position",
    "profit",
    "sell",
    "signature",
    "signing",
    "size",
    "trade",
    "wallet",
}


# Compound names that must never appear in generated reports, in any case or
# delimiter style.  Detection uses normalized substring search so that names
# embedded inside larger identifiers (``graph_hint_is_paper_candidate``)
# are caught.
PROHIBITED_REPORT_PHRASES = {
    "cancel_order",
    "evaluator_ready",
    "exact_same_payoff",
    "executable_arb",
    "fill_size",
    "paper_candidate",
    "place_order",
    "possible_arb",
    "profit_usd",
    "size_usd",
    "trade_permission",
    "trusted_relationship",
}


_TOKEN_PATTERNS = {token: re.compile(rf"\b{re.escape(token)}\b") for token in PROHIBITED_REPORT_TOKENS}


def _normalize(value: str) -> str:
    """Case-fold and normalize delimiters for safety detection."""

    return value.lower().replace("-", "_")


def _matches_phrase(normalized: str, phrase: str) -> bool:
    return phrase in normalized


def contains_prohibited_report_token(value: str) -> bool:
    """Return True if ``value`` contains any prohibited word or phrase.

    Single-word tokens are matched with ``\\btoken\\b`` (so ``recorder`` is
    safe), while compound phrases are matched with substring search after
    delimiter normalisation (so ``graph_hint_is_paper_candidate`` and
    ``is-paper-candidate-v2`` are both rejected).
    """

    normalized = _normalize(value)
    for pattern in _TOKEN_PATTERNS.values():
        if pattern.search(normalized):
            return True
    for phrase in PROHIBITED_REPORT_PHRASES:
        if _matches_phrase(normalized, phrase):
            return True
    return False


def find_prohibited_report_tokens(payload: Any) -> list[str]:
    """Recursively report every key/value path that contains a prohibited term."""

    findings: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if contains_prohibited_report_token(str(key)):
                    findings.append(f"{path}.{key}" if path else str(key))
                visit(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif isinstance(value, str) and contains_prohibited_report_token(value):
            findings.append(path)

    visit(payload, "")
    return sorted(set(findings))


def find_prohibited_report_keys(payload: Any) -> list[str]:
    """Report any key whose entire normalized name is a prohibited term."""

    findings: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = _normalize(str(key))
                if (
                    normalized in PROHIBITED_REPORT_TOKENS
                    or normalized in PROHIBITED_REPORT_PHRASES
                ):
                    findings.append(f"{path}.{key}" if path else str(key))
                visit(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload, "")
    return sorted(set(findings))


def find_prohibited_rendered_text(text: str) -> list[str]:
    """Return every prohibited token or phrase found in rendered Markdown.

    This is the same logic as :func:`contains_prohibited_report_token` but
    returns the actual matched terms so that pre-write Markdown validators
    can surface the offending vocabulary in their error messages.
    """

    normalized = _normalize(text)
    hits: list[str] = []
    for token, pattern in _TOKEN_PATTERNS.items():
        if pattern.search(normalized):
            hits.append(token)
    for phrase in PROHIBITED_REPORT_PHRASES:
        if _matches_phrase(normalized, phrase):
            hits.append(phrase)
    return sorted(set(hits))


__all__ = [
    "PROHIBITED_REPORT_PHRASES",
    "PROHIBITED_REPORT_TOKENS",
    "contains_prohibited_report_token",
    "find_prohibited_rendered_text",
    "find_prohibited_report_keys",
    "find_prohibited_report_tokens",
]
