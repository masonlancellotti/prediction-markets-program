"""Static audit: every literal `python main.py ...` recommendation in source must reference
a real subcommand and real flags as declared by main.build_parser().

This catches the class of bug where an emitter recommends a flag/subcommand that does
not exist (e.g. `python main.py build-exact-settlements --weather-only`). It is a
structural check — it verifies subcommand + flag names, not that placeholder values
satisfy argparse type/choices constraints.

Runtime-built recommendations (lines containing string concatenation with ` + `) are
skipped; those need execution-path coverage in a runtime test, not a static scanner.
"""
from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

from main import build_parser


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIR_SEGMENTS = {".venv", "__pycache__", "reports", "scripts", "tests", "build", "dist"}
PATTERN = re.compile(r"python main\.py ([^\"'`\n]+)")


def _candidate_commands() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        if any(seg in SKIP_DIR_SEGMENTS for seg in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for ln, line in enumerate(text.splitlines(), start=1):
            if " + " in line:
                continue
            normalized_line = re.sub(r"\{[^{}]+\}", "PLACEHOLDER", line)
            for m in PATTERN.finditer(normalized_line):
                raw = m.group(1).strip().rstrip(".,;:)")
                raw = re.sub(r"\s+", " ", raw)
                if raw:
                    out.append((str(path.relative_to(PROJECT_ROOT)), ln, raw))
    return out


def _subparsers_map(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _known_flags(subparser: argparse.ArgumentParser) -> set[str]:
    flags: set[str] = set()
    for action in subparser._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                flags.add(opt)
    return flags


def _extract_subcommand_and_flags(raw: str) -> tuple[str, list[str]] | None:
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None
    if not tokens:
        return None
    subcommand = tokens[0]
    flags = [tok for tok in tokens[1:] if tok.startswith("--")]
    return subcommand, flags


def test_every_literal_cli_recommendation_parses_against_real_argparse():
    parser = build_parser()
    subparsers = _subparsers_map(parser)
    assert subparsers, "build_parser() exposed no subcommands — refactor regression"

    bad: list[str] = []
    seen_any = False
    for path, ln, raw in _candidate_commands():
        seen_any = True
        parsed = _extract_subcommand_and_flags(raw)
        if parsed is None:
            continue
        subcommand, flags = parsed
        if subcommand not in subparsers:
            bad.append(f"{path}:{ln} unknown subcommand '{subcommand}' -> python main.py {raw}")
            continue
        known = _known_flags(subparsers[subcommand])
        for flag in flags:
            if flag not in known:
                bad.append(f"{path}:{ln} unknown flag '{flag}' for '{subcommand}' -> python main.py {raw}")

    assert seen_any, "scanner found zero candidates — pattern broke?"
    assert not bad, (
        "Invalid `python main.py ...` recommendations in source. Each subcommand and "
        "flag must exist in main.build_parser(). Fix the emitter (preferred) or refactor "
        "the literal:\n  " + "\n  ".join(bad)
    )
