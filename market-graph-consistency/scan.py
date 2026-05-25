from __future__ import annotations

import argparse
from pathlib import Path

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.loader import load_fixture_markets
from graph_engine.relationships.registry import load_relationship_registry
from graph_engine.reporting.diagnostic_diff import write_diagnostic_diff_report
from graph_engine.reporting.hint_diff import render_console_summary, write_hint_diff_report
from graph_engine.reporting.hints import write_relative_value_hints_report
from graph_engine.reporting.formula_watchlist import write_formula_watchlist_reports
from graph_engine.reporting.json_report import write_json_report
from graph_engine.reporting.md_report import write_markdown_report
from graph_engine.snapshot_loader import NoUsableSnapshotsFound, load_schema_v1_snapshots


PROJECT_ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = PROJECT_ROOT / "venues" / "fixtures"
RELATIONSHIPS_DIR = PROJECT_ROOT / "relationships"
REPORTS_DIR = PROJECT_ROOT / "reports"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline semantic market graph consistency scanner.")
    subparsers = parser.add_subparsers(dest="command")
    diff_parser = subparsers.add_parser("diff-relative-value-hints", help="Compare two saved relative-value hint reports.")
    diff_parser.add_argument("--old", required=True, type=Path, help="Older saved relative-value hint JSON report.")
    diff_parser.add_argument("--new", required=True, type=Path, help="Newer saved relative-value hint JSON report.")
    diff_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_hint_diff.json",
        help="Path for the diagnostic JSON diff report.",
    )
    diff_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_hint_diff.md",
        help="Path for the diagnostic Markdown diff report.",
    )
    diagnostic_diff_parser = subparsers.add_parser("diff-diagnostics", help="Compare two saved graph diagnostic JSON reports.")
    diagnostic_diff_parser.add_argument("--old", required=True, type=Path, help="Older saved diagnostic JSON report.")
    diagnostic_diff_parser.add_argument("--new", required=True, type=Path, help="Newer saved diagnostic JSON report.")
    diagnostic_diff_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_diagnostic_diff.json",
        help="Path for the diagnostic JSON diff report.",
    )
    diagnostic_diff_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_diagnostic_diff.md",
        help="Path for the diagnostic Markdown diff report.",
    )
    parser.add_argument(
        "--snapshots-dir",
        type=Path,
        help="Directory of saved schema-v1 normalized snapshot JSON files to inspect.",
    )
    parser.add_argument(
        "--snapshot-file",
        action="append",
        type=Path,
        default=[],
        help="Explicit saved schema-v1 normalized snapshot JSON file. May be provided more than once.",
    )
    return parser.parse_args(argv)


def _load_fixture_mode():
    snapshot, source_metadata = load_fixture_markets(FIXTURES_DIR)
    registry = load_relationship_registry(
        RELATIONSHIPS_DIR,
        known_market_ids=set(snapshot.nodes),
    )
    snapshot.edges = registry.edges
    snapshot.exclusion_sets = registry.exclusion_sets
    return snapshot, source_metadata, "fixtures"


def _load_snapshot_mode(args: argparse.Namespace):
    try:
        snapshot, source_metadata = load_schema_v1_snapshots(
            snapshots_dir=args.snapshots_dir,
            snapshot_paths=args.snapshot_file,
        )
    except NoUsableSnapshotsFound as exc:
        print(f"No usable schema-v1 snapshots found ({exc}); falling back to bundled fixtures.")
        return _load_fixture_mode()

    print("Loaded saved schema-v1 snapshots in read-only inspection mode.")
    print("Relationship loading is disabled for saved snapshot prototype mode.")
    return snapshot, source_metadata, "saved_snapshots"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "diff-relative-value-hints":
        report = write_hint_diff_report(args.old, args.new, args.json_output, args.markdown_output)
        print(render_console_summary(report))
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "diff-diagnostics":
        report = write_diagnostic_diff_report(args.old, args.new, args.json_output, args.markdown_output)
        summary = report["summary"]
        print("Mode: saved diagnostic diff")
        print(f"Added constraints: {summary['added_count']}")
        print(f"Removed constraints: {summary['removed_count']}")
        print(f"Changed constraints: {summary['changed_count']}")
        print(f"Unchanged constraints: {summary['unchanged_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0

    if args.snapshots_dir or args.snapshot_file:
        snapshot, source_metadata, mode = _load_snapshot_mode(args)
    else:
        snapshot, source_metadata, mode = _load_fixture_mode()

    violations = run_consistency_checks(snapshot)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / "graph_consistency_summary.json"
    md_path = REPORTS_DIR / "graph_consistency_summary.md"
    diagnostics_json_path = REPORTS_DIR / "market_graph_consistency_diagnostics.json"
    diagnostics_md_path = REPORTS_DIR / "market_graph_consistency_diagnostics.md"
    hints_json_path = REPORTS_DIR / "market_graph_relative_value_hints.json"
    hints_md_path = REPORTS_DIR / "market_graph_relative_value_hints.md"
    formula_watchlist_json_path = REPORTS_DIR / "market_graph_formula_watchlist.json"
    formula_watchlist_md_path = REPORTS_DIR / "market_graph_formula_watchlist.md"
    investigation_requests_json_path = REPORTS_DIR / "rel_value_investigation_requests.json"
    investigation_requests_md_path = REPORTS_DIR / "rel_value_investigation_requests.md"

    write_json_report(snapshot, violations, json_path, source_metadata)
    write_markdown_report(snapshot, violations, md_path)
    write_json_report(snapshot, violations, diagnostics_json_path, source_metadata)
    write_markdown_report(snapshot, violations, diagnostics_md_path)
    write_relative_value_hints_report(snapshot, violations, hints_json_path, hints_md_path)
    write_formula_watchlist_reports(
        snapshot,
        formula_watchlist_json_path,
        formula_watchlist_md_path,
        investigation_requests_json_path,
        investigation_requests_md_path,
    )

    print(f"Mode: {mode}")
    print(f"Loaded {len(snapshot.nodes)} markets, {len(snapshot.edges)} edges, {len(snapshot.exclusion_sets)} exclusion sets.")
    print(f"Found {len(violations)} review findings.")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {diagnostics_json_path}")
    print(f"Wrote {diagnostics_md_path}")
    print(f"Wrote {hints_json_path}")
    print(f"Wrote {hints_md_path}")
    print(f"Wrote {formula_watchlist_json_path}")
    print(f"Wrote {formula_watchlist_md_path}")
    print(f"Wrote {investigation_requests_json_path}")
    print(f"Wrote {investigation_requests_md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
