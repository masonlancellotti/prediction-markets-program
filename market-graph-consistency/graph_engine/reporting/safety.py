from __future__ import annotations

import re
from typing import Any


# Central report safety vocabulary. These tokens are rejected in generated report
# keys and string values by graph-local validators.
PROHIBITED_REPORT_TOKENS = {
    "arb",
    "buy",
    "cancel_order",
    "dollars",
    "edge_bps",
    "evaluator_ready",
    "exact_same_payoff",
    "executable",
    "executable_arb",
    "fill",
    "fill_size",
    "order",
    "paper",
    "paper_candidate",
    "place_order",
    "pnl",
    "position",
    "profit",
    "profit_usd",
    "possible_arb",
    "sell",
    "signature",
    "signing",
    "size",
    "size_usd",
    "trade",
    "trade_permission",
    "trusted_relationship",
    "wallet",
}


def contains_prohibited_report_token(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in PROHIBITED_REPORT_TOKENS)


def find_prohibited_report_tokens(payload: Any) -> list[str]:
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
    findings: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in PROHIBITED_REPORT_TOKENS:
                    findings.append(f"{path}.{key}" if path else str(key))
                visit(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload, "")
    return sorted(set(findings))
