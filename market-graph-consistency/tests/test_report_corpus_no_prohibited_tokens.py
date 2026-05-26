"""Corpus-level fail-closed test for graph reports.

The per-module validators in ``graph_engine.reporting`` already reject
prohibited tokens at write time, but a future module could quietly bypass
validation by writing a report through a custom path. This sweep walks every
JSON and Markdown file under ``reports/`` and asserts the central prohibited
vocabulary never appears in keys, values, or rendered text.

The test runs the fixture scan first so the corpus is always populated and
deterministic across machines.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from graph_engine.reporting.safety import (
    PROHIBITED_REPORT_PHRASES,
    PROHIBITED_REPORT_TOKENS,
    find_prohibited_rendered_text,
    find_prohibited_report_tokens,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "reports"


# Markdown reports legitimately reference prohibited *tokens* in their
# diagnostic banners (e.g. the BTC basis-risk packet's
# "not_evaluator_input"-style blocker text contains "input"). The sweep only
# flags substrings the safety module classifies as prohibited; legitimate
# diagnostic vocabulary like ``MANUAL_REVIEW`` is unaffected.
#
# Some reports embed external market titles verbatim (LLM review packets ship
# the raw market title text so a human reviewer can read what the model saw).
# Those files are explicitly excluded because the safety vocabulary is about
# *graph-emitted* labels, not external market title text. Excluded files must
# still validate against their per-module schema — that contract still
# requires diagnostic_only/allowed_actions safety envelopes.
EXTERNAL_TITLE_PASSTHROUGH_FILES = frozenset(
    {
        "llm_relationship_review_packets.jsonl",
        "llm_relationship_hypotheses_validated.json",
    }
)


@pytest.fixture(scope="module", autouse=True)
def _regenerate_reports() -> None:
    """Run the default fixture scan so the corpus reflects current code.

    The scan is offline and deterministic. We invoke it as a subprocess to
    avoid leaking sys.path / argparse state into the test process.
    """

    if not REPORTS_DIR.exists():
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scan.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"scan.py failed: {result.stderr}\n{result.stdout}"


def _iter_corpus_files() -> list[Path]:
    if not REPORTS_DIR.exists():
        return []
    return sorted(
        path
        for path in REPORTS_DIR.iterdir()
        if path.is_file() and path.suffix in {".json", ".md", ".jsonl", ".csv"}
    )


def test_corpus_contains_at_least_one_report() -> None:
    files = _iter_corpus_files()
    assert files, "scan.py should produce at least one report"


def test_every_json_report_has_no_prohibited_tokens() -> None:
    offenders: list[str] = []
    for path in _iter_corpus_files():
        if path.suffix != ".json":
            continue
        if path.name in EXTERNAL_TITLE_PASSTHROUGH_FILES:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        findings = find_prohibited_report_tokens(payload)
        if findings:
            offenders.append(f"{path.name}: {findings}")
    assert not offenders, "Prohibited tokens leaked into JSON reports:\n" + "\n".join(offenders)


def test_every_jsonl_report_has_no_prohibited_tokens() -> None:
    offenders: list[str] = []
    for path in _iter_corpus_files():
        if path.suffix != ".jsonl":
            continue
        if path.name in EXTERNAL_TITLE_PASSTHROUGH_FILES:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            findings = find_prohibited_report_tokens(payload)
            if findings:
                offenders.append(f"{path.name}:{line_number}: {findings}")
    assert not offenders, "Prohibited tokens leaked into JSONL reports:\n" + "\n".join(offenders)


def test_every_markdown_report_has_no_prohibited_text() -> None:
    offenders: list[str] = []
    for path in _iter_corpus_files():
        if path.suffix != ".md":
            continue
        if path.name in EXTERNAL_TITLE_PASSTHROUGH_FILES:
            continue
        findings = find_prohibited_rendered_text(path.read_text(encoding="utf-8"))
        if findings:
            offenders.append(f"{path.name}: {findings}")
    assert not offenders, "Prohibited rendered text leaked into Markdown reports:\n" + "\n".join(offenders)


def test_every_csv_report_has_no_prohibited_text() -> None:
    offenders: list[str] = []
    for path in _iter_corpus_files():
        if path.suffix != ".csv":
            continue
        if path.name in EXTERNAL_TITLE_PASSTHROUGH_FILES:
            continue
        findings = find_prohibited_rendered_text(path.read_text(encoding="utf-8"))
        if findings:
            offenders.append(f"{path.name}: {findings}")
    assert not offenders, "Prohibited rendered text leaked into CSV reports:\n" + "\n".join(offenders)


def test_external_title_passthrough_files_still_carry_safety_envelope() -> None:
    """Excluded files still must carry diagnostic_only/allowed_actions envelopes.

    The sweep ignores prohibited tokens inside market titles for these files
    but the safety contract still requires the wrapper to declare review-only
    status. Confirm that contract is not silently weakened by the exclusion.
    """

    for filename in EXTERNAL_TITLE_PASSTHROUGH_FILES:
        path = REPORTS_DIR / filename
        if not path.exists():
            continue
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload.get("diagnostic_only") is True, f"{filename} must remain diagnostic_only"
            assert payload.get("affects_evaluator_gates") is False, (
                f"{filename} must declare affects_evaluator_gates=false"
            )
            assert payload.get("allowed_actions") == ["WATCH", "MANUAL_REVIEW"], (
                f"{filename} must remain WATCH/MANUAL_REVIEW only"
            )
        elif path.suffix == ".jsonl":
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            for line in lines:
                record = json.loads(line)
                assert record.get("diagnostic_only") is True, (
                    f"{filename} record must remain diagnostic_only"
                )
                assert record.get("allowed_actions") == ["WATCH", "MANUAL_REVIEW"], (
                    f"{filename} record must remain WATCH/MANUAL_REVIEW only"
                )


def test_safety_vocabulary_is_non_empty_invariant() -> None:
    """Guard against accidental shrinkage of the prohibited vocabulary.

    A future contributor must not lower the safety bar by deleting tokens.
    The set may grow (new prohibited names) but it should not shrink below
    its current floor without an explicit reason.
    """

    floor_tokens = {
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
        "size",
        "trade",
        "wallet",
    }
    floor_phrases = {
        "cancel_order",
        "evaluator_ready",
        "exact_same_payoff",
        "executable_arb",
        "paper_candidate",
        "place_order",
        "possible_arb",
        "trade_permission",
        "trusted_relationship",
    }
    assert floor_tokens.issubset(PROHIBITED_REPORT_TOKENS), (
        "PROHIBITED_REPORT_TOKENS must not shrink below the safety floor"
    )
    assert floor_phrases.issubset(PROHIBITED_REPORT_PHRASES), (
        "PROHIBITED_REPORT_PHRASES must not shrink below the safety floor"
    )
