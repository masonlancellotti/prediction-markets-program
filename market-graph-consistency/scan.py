from __future__ import annotations

import argparse
import json
from pathlib import Path

from graph_engine.bounded_noarb import write_bounded_noarb_report
from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.loader import load_fixture_markets
from graph_engine.relationships.registry import load_relationship_registry
from graph_engine.reporting.diagnostic_diff import write_diagnostic_diff_report
from graph_engine.reporting.event_entity_ontology import write_event_entity_ontology_report
from graph_engine.reporting.hint_diff import render_console_summary, write_hint_diff_report
from graph_engine.reporting.hints import write_relative_value_hints_report
from graph_engine.reporting.formula_watchlist import write_formula_watchlist_reports
from graph_engine.reporting.json_report import write_json_report
from graph_engine.reporting.llm_relationship_hypotheses import (
    write_imported_llm_relationship_hypotheses_report,
    write_llm_relationship_hypotheses_report,
    write_llm_relationship_review_packets,
)
from graph_engine.reporting.md_report import write_markdown_report
from graph_engine.reporting.ops_status import write_market_graph_ops_status_report
from graph_engine.reporting.platform_expansion_radar import (
    write_family_inference_audit_report,
    write_platform_expansion_radar_report,
)
from graph_engine.reporting.payoff_state_diff import write_payoff_state_diff_report
from graph_engine.reporting.payoff_state_feasibility_bridge import write_payoff_state_feasibility_bridge_report
from graph_engine.reporting.payoff_state_report import write_payoff_state_diagnostics_report
from graph_engine.reporting.probability_constraints import write_probability_constraints_report
from graph_engine.reporting.relative_value_investigation_packets import (
    write_graph_to_relative_value_investigation_packets_report,
)
from graph_engine.reporting.rv_diagnostic_ingest import (
    write_rv_diagnostic_relationship_edges_report,
)
from graph_engine.reporting.rv_review_worklist import write_rv_review_worklist_report
from graph_engine.reporting.llm_graph_relationship_review import (
    validate_llm_graph_relationship_review_output,
    write_llm_graph_relationship_review_assets,
)
from graph_engine.reporting.manual_relationship_evidence import (
    write_graph_manual_relationship_evidence_report,
)
from graph_engine.reporting.manual_discovery_backlog import (
    write_graph_manual_discovery_backlog_report,
)
from graph_engine.reporting.llm_graph_manual_evidence_review import (
    validate_llm_graph_manual_evidence_review_output,
    write_llm_graph_manual_evidence_review_assets,
)
from graph_engine.reporting.saved_quote_overlay_status import write_saved_quote_overlay_status_report
from graph_engine.reporting.signal_persistence import write_signal_persistence_report
from graph_engine.reporting.stale_lag_watchlist import build_stale_lag_watchlist_report, write_stale_lag_watchlist_report
from graph_engine.reporting.trade_indicators import write_trade_indicator_report
from graph_engine.reporting.venue_native_groups import write_venue_native_exhaustive_groups_report
from graph_engine.reporting.venue_lag import write_venue_lag_watchlist_report
from graph_engine.state_family_registry import write_state_family_registry_report
from graph_engine.snapshot_loader import (
    NoUsableSnapshotsFound,
    apply_real_quote_fixture_overlay,
    load_schema_v1_snapshots,
)


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
    payoff_diff_parser = subparsers.add_parser(
        "diff-payoff-state",
        help="Compare two saved payoff-state diagnostic JSON reports.",
    )
    payoff_diff_parser.add_argument("--old", required=True, type=Path, help="Older saved payoff-state diagnostic JSON report.")
    payoff_diff_parser.add_argument("--new", required=True, type=Path, help="Newer saved payoff-state diagnostic JSON report.")
    payoff_diff_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_payoff_state_diff.json",
        help="Path for the diagnostic payoff-state diff JSON report.",
    )
    payoff_diff_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_payoff_state_diff.md",
        help="Path for the diagnostic payoff-state diff Markdown report.",
    )
    venue_lag_parser = subparsers.add_parser("venue-lag-watchlist", help="Build a saved-file venue lag watchlist.")
    venue_lag_parser.add_argument(
        "--input",
        action="append",
        type=Path,
        required=True,
        help="Saved schema-v1 snapshot or saved diagnostic JSON file. Provide two or more.",
    )
    venue_lag_parser.add_argument(
        "--stale-seconds",
        type=int,
        default=30 * 60,
        help="Quote age threshold for the diagnostic.",
    )
    venue_lag_parser.add_argument(
        "--price-delta-threshold",
        type=float,
        default=0.10,
        help="Observed related-market movement threshold.",
    )
    venue_lag_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_venue_lag_watchlist.json",
        help="Path for the venue lag JSON watchlist.",
    )
    venue_lag_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_venue_lag_watchlist.md",
        help="Path for the venue lag Markdown watchlist.",
    )
    llm_import_parser = subparsers.add_parser(
        "import-llm-hypotheses",
        help="Validate a saved offline LLM relationship hypothesis JSON/JSONL file.",
    )
    llm_import_parser.add_argument("--input", required=True, type=Path, help="Saved LLM hypothesis JSON or JSONL file.")
    llm_import_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "llm_relationship_hypotheses_validated.json",
        help="Path for the validated LLM hypothesis report.",
    )
    signal_persistence_parser = subparsers.add_parser(
        "write-signal-persistence-report",
        help="Compare saved market graph signal reports and write a diagnostic persistence report.",
    )
    signal_persistence_parser.add_argument(
        "--current",
        action="append",
        type=Path,
        required=True,
        help="Current saved diagnostic signal report. May be provided more than once.",
    )
    signal_persistence_parser.add_argument(
        "--previous",
        action="append",
        type=Path,
        default=[],
        help="Previous saved diagnostic signal report. Missing files are treated as first-run baseline.",
    )
    signal_persistence_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_signal_persistence.json",
        help="Path for the signal persistence JSON report.",
    )
    signal_persistence_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_signal_persistence.md",
        help="Path for the signal persistence Markdown report.",
    )
    rv_packet_parser = subparsers.add_parser(
        "write-graph-rv-investigation-packets",
        help="Package saved graph diagnostics into relative-value investigation packets.",
    )
    rv_packet_parser.add_argument(
        "--trade-indicators",
        type=Path,
        default=REPORTS_DIR / "market_graph_trade_indicators.json",
        help="Saved market graph signal report.",
    )
    rv_packet_parser.add_argument(
        "--probability-constraints",
        type=Path,
        default=REPORTS_DIR / "market_graph_probability_constraints.json",
        help="Saved probability constraint report.",
    )
    rv_packet_parser.add_argument(
        "--llm-hypotheses",
        type=Path,
        default=REPORTS_DIR / "llm_relationship_hypotheses_validated.json",
        help="Saved offline LLM hypothesis validation report.",
    )
    rv_packet_parser.add_argument(
        "--signal-persistence",
        type=Path,
        default=REPORTS_DIR / "market_graph_signal_persistence.json",
        help="Saved signal persistence report.",
    )
    rv_packet_parser.add_argument(
        "--event-entity-ontology",
        type=Path,
        help="Optional saved event/entity ontology report for packet entity cross-links.",
    )
    rv_packet_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "graph_to_relative_value_investigation_packets.json",
        help="Path for graph-to-relative-value packet JSON report.",
    )
    rv_packet_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "graph_to_relative_value_investigation_packets.md",
        help="Path for graph-to-relative-value packet Markdown report.",
    )
    platform_radar_parser = subparsers.add_parser(
        "write-platform-expansion-radar",
        help="Build a saved-file platform expansion radar from graph reports.",
    )
    platform_radar_parser.add_argument(
        "--relative-value-reports-dir",
        type=Path,
        help="Optional directory of saved relative-value reports to read.",
    )
    platform_radar_parser.add_argument(
        "--trade-indicators",
        type=Path,
        default=REPORTS_DIR / "market_graph_trade_indicators.json",
        help="Saved market graph signal report.",
    )
    platform_radar_parser.add_argument(
        "--probability-constraints",
        type=Path,
        default=REPORTS_DIR / "market_graph_probability_constraints.json",
        help="Saved probability constraint report.",
    )
    platform_radar_parser.add_argument(
        "--rv-investigation-packets",
        type=Path,
        default=REPORTS_DIR / "graph_to_relative_value_investigation_packets.json",
        help="Saved graph-to-relative-value packet report.",
    )
    platform_radar_parser.add_argument(
        "--state-family-registry",
        type=Path,
        default=REPORTS_DIR / "market_graph_state_family_registry.json",
        help="Saved state-family registry report.",
    )
    platform_radar_parser.add_argument(
        "--signal-persistence",
        type=Path,
        default=REPORTS_DIR / "market_graph_signal_persistence.json",
        help="Saved signal persistence report.",
    )
    platform_radar_parser.add_argument(
        "--event-entity-ontology",
        type=Path,
        help="Optional saved event/entity ontology report for platform radar priority tie-breaks.",
    )
    platform_radar_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_platform_expansion_radar.json",
        help="Path for platform expansion radar JSON report.",
    )
    platform_radar_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_platform_expansion_radar.md",
        help="Path for platform expansion radar Markdown report.",
    )
    family_audit_parser = subparsers.add_parser(
        "write-family-inference-audit",
        help="Build a saved-file audit of platform-radar family inference inputs.",
    )
    family_audit_parser.add_argument(
        "--trade-indicators",
        type=Path,
        default=REPORTS_DIR / "market_graph_trade_indicators.json",
        help="Saved market graph signal report.",
    )
    family_audit_parser.add_argument(
        "--probability-constraints",
        type=Path,
        default=REPORTS_DIR / "market_graph_probability_constraints.json",
        help="Saved probability constraint report.",
    )
    family_audit_parser.add_argument(
        "--rv-investigation-packets",
        type=Path,
        default=REPORTS_DIR / "graph_to_relative_value_investigation_packets.json",
        help="Saved graph-to-relative-value packet report.",
    )
    family_audit_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_family_inference_audit.json",
        help="Path for family inference audit JSON report.",
    )
    ontology_parser = subparsers.add_parser(
        "write-event-entity-ontology",
        help="Build a saved-file event/entity ontology report from graph snapshots.",
    )
    ontology_parser.add_argument(
        "--llm-hypotheses",
        type=Path,
        default=REPORTS_DIR / "llm_relationship_hypotheses_validated.json",
        help="Optional saved offline LLM hypothesis validation report.",
    )
    ontology_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_event_entity_ontology.json",
        help="Path for event/entity ontology JSON report.",
    )
    ontology_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_event_entity_ontology.md",
        help="Path for event/entity ontology Markdown report.",
    )
    stale_lag_parser = subparsers.add_parser(
        "write-stale-lag-watchlist",
        help="Build a saved-file deterministic stale/lag watchlist from graph snapshots.",
    )
    stale_lag_parser.add_argument(
        "--llm-hypotheses",
        type=Path,
        default=REPORTS_DIR / "llm_relationship_hypotheses_validated.json",
        help="Optional saved offline LLM hypothesis validation report.",
    )
    stale_lag_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_stale_lag_watchlist.json",
        help="Path for stale/lag JSON report.",
    )
    stale_lag_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "market_graph_stale_lag_watchlist.md",
        help="Path for stale/lag Markdown report.",
    )
    rv_ingest_parser = subparsers.add_parser(
        "ingest-relative-value-diagnostics",
        help="Ingest saved relative-value diagnostic reports into the graph relationship layer.",
    )
    rv_ingest_parser.add_argument(
        "--rv-reports-dir",
        type=Path,
        required=True,
        help="Directory of saved relative-value-scanner reports (saved files only).",
    )
    rv_ingest_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "rv_diagnostic_relationship_edges.json",
        help="Path for the RV diagnostic relationship edges JSON report.",
    )
    rv_ingest_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "rv_diagnostic_relationship_edges.md",
        help="Path for the RV diagnostic relationship edges Markdown report.",
    )
    rv_worklist_parser = subparsers.add_parser(
        "export-rv-review-worklist",
        help="Rank RV-ingested relationship edges as a graph-to-RV worklist.",
    )
    rv_worklist_parser.add_argument(
        "--edges",
        type=Path,
        default=REPORTS_DIR / "rv_diagnostic_relationship_edges.json",
        help="Saved RV diagnostic relationship edges JSON report.",
    )
    rv_worklist_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "rv_review_worklist.json",
        help="Path for the RV review worklist JSON report.",
    )
    rv_worklist_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "rv_review_worklist.md",
        help="Path for the RV review worklist Markdown report.",
    )
    rv_worklist_parser.add_argument(
        "--include-queued-ibkr",
        action="store_true",
        help="Include queued IBKR/ForecastEx edges in the worklist (default: excluded).",
    )
    llm_review_parser = subparsers.add_parser(
        "llm-review-graph-relationships",
        help="Generate an offline LLM review prompt and JSON schema for graph relationship edges.",
    )
    llm_review_parser.add_argument(
        "--input",
        type=Path,
        default=REPORTS_DIR / "rv_diagnostic_relationship_edges.json",
        help="Saved RV diagnostic relationship edges JSON report.",
    )
    llm_review_parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Max number of edges to embed in the LLM review prompt.",
    )
    llm_review_parser.add_argument(
        "--prompt-output",
        type=Path,
        default=REPORTS_DIR / "llm_graph_relationship_review_prompt.md",
        help="Path for the offline LLM review prompt Markdown file.",
    )
    llm_review_parser.add_argument(
        "--expected-json-schema-output",
        type=Path,
        default=REPORTS_DIR / "llm_graph_relationship_review_schema.json",
        help="Path for the strict JSON schema for LLM output validation.",
    )
    llm_validate_parser = subparsers.add_parser(
        "validate-llm-graph-relationship-review",
        help="Validate a saved LLM graph relationship review JSON output against the schema and safety contract.",
    )
    llm_validate_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Saved LLM relationship review JSON output.",
    )
    llm_validate_parser.add_argument(
        "--schema",
        type=Path,
        default=REPORTS_DIR / "llm_graph_relationship_review_schema.json",
        help="Saved LLM relationship review JSON schema.",
    )
    llm_validate_parser.add_argument(
        "--edges",
        type=Path,
        default=REPORTS_DIR / "rv_diagnostic_relationship_edges.json",
        help="Saved RV diagnostic edges report (used for deterministic-edge cross-checks).",
    )
    llm_validate_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "llm_graph_relationship_review_validation.json",
        help="Path for the LLM validation JSON report.",
    )
    manual_evidence_parser = subparsers.add_parser(
        "graph-manual-relationship-evidence",
        help="Build the graph manual relationship evidence inventory from saved RV reports.",
    )
    manual_evidence_parser.add_argument(
        "--rv-reports-dir",
        type=Path,
        required=True,
        help="Directory of saved relative-value-scanner reports.",
    )
    manual_evidence_parser.add_argument(
        "--edges",
        type=Path,
        default=REPORTS_DIR / "rv_diagnostic_relationship_edges.json",
        help="Optional saved graph RV-edges report (used to seed records).",
    )
    manual_evidence_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "graph_manual_relationship_evidence.json",
        help="Path for the JSON evidence inventory.",
    )
    manual_evidence_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "graph_manual_relationship_evidence.md",
        help="Path for the Markdown evidence inventory.",
    )
    manual_backlog_parser = subparsers.add_parser(
        "graph-manual-discovery-backlog",
        help="Rank manual discovery tasks from the manual evidence inventory.",
    )
    manual_backlog_parser.add_argument(
        "--relationships",
        type=Path,
        default=REPORTS_DIR / "graph_manual_relationship_evidence.json",
        help="Saved manual evidence inventory JSON.",
    )
    manual_backlog_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "graph_manual_discovery_backlog.json",
        help="Path for the manual discovery backlog JSON.",
    )
    manual_backlog_parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPORTS_DIR / "graph_manual_discovery_backlog.md",
        help="Path for the manual discovery backlog Markdown.",
    )
    llm_manual_review_parser = subparsers.add_parser(
        "llm-review-graph-manual-evidence",
        help="Generate an offline LLM review prompt + schema for the manual evidence inventory.",
    )
    llm_manual_review_parser.add_argument(
        "--input",
        type=Path,
        default=REPORTS_DIR / "graph_manual_relationship_evidence.json",
        help="Saved manual evidence inventory JSON.",
    )
    llm_manual_review_parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Max number of records to embed in the prompt.",
    )
    llm_manual_review_parser.add_argument(
        "--prompt-output",
        type=Path,
        default=REPORTS_DIR / "llm_graph_manual_evidence_prompt.md",
        help="Path for the offline LLM review prompt Markdown file.",
    )
    llm_manual_review_parser.add_argument(
        "--expected-json-schema-output",
        type=Path,
        default=REPORTS_DIR / "llm_graph_manual_evidence_schema.json",
        help="Path for the strict JSON schema for LLM output validation.",
    )
    llm_manual_validate_parser = subparsers.add_parser(
        "validate-llm-graph-manual-evidence-review",
        help="Validate a saved LLM manual-evidence review JSON output against the schema and safety contract.",
    )
    llm_manual_validate_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Saved LLM manual-evidence review JSON output.",
    )
    llm_manual_validate_parser.add_argument(
        "--schema",
        type=Path,
        default=REPORTS_DIR / "llm_graph_manual_evidence_schema.json",
        help="Saved LLM manual-evidence review JSON schema.",
    )
    llm_manual_validate_parser.add_argument(
        "--relationships",
        type=Path,
        default=REPORTS_DIR / "graph_manual_relationship_evidence.json",
        help="Saved manual evidence inventory JSON (used for deterministic-record cross-checks).",
    )
    llm_manual_validate_parser.add_argument(
        "--json-output",
        type=Path,
        default=REPORTS_DIR / "llm_graph_manual_evidence_validation.json",
        help="Path for the LLM manual-evidence validation JSON report.",
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
    parser.add_argument(
        "--real-quote-fixtures-dir",
        type=Path,
        help="Optional saved schema-v1 quote fixture directory to overlay onto bundled fixtures.",
    )
    return parser.parse_args(argv)


def _load_existing_llm_hypotheses_report(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("validated_hypotheses"):
        return None
    return payload


def _load_saved_json_report(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_fixture_mode(real_quote_fixtures_dir: Path | None = None):
    snapshot, source_metadata = load_fixture_markets(FIXTURES_DIR)
    registry = load_relationship_registry(
        RELATIONSHIPS_DIR,
        known_market_ids=set(snapshot.nodes),
    )
    snapshot.edges = registry.edges
    snapshot.exclusion_sets = registry.exclusion_sets
    if real_quote_fixtures_dir is None:
        return snapshot, source_metadata, "fixtures"

    snapshot, overlay_metadata = apply_real_quote_fixture_overlay(snapshot, real_quote_fixtures_dir)
    source_metadata = [
        *source_metadata,
        {"file": "real_quote_fixture_overlay", **overlay_metadata},
    ]
    return snapshot, source_metadata, "fixtures_with_real_quote_fixtures"


def _load_snapshot_mode(args: argparse.Namespace):
    try:
        snapshot, source_metadata = load_schema_v1_snapshots(
            snapshots_dir=args.snapshots_dir,
            snapshot_paths=args.snapshot_file,
        )
    except NoUsableSnapshotsFound as exc:
        print(f"No usable schema-v1 snapshots found ({exc}); falling back to bundled fixtures.")
        return _load_fixture_mode(getattr(args, "real_quote_fixtures_dir", None))

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
    if args.command == "diff-payoff-state":
        report = write_payoff_state_diff_report(args.old, args.new, args.json_output, args.markdown_output)
        summary = report["summary"]
        print("Mode: saved payoff-state diagnostic diff")
        print(f"Added families: {summary['added_count']}")
        print(f"Removed families: {summary['removed_count']}")
        print(f"Changed families: {summary['changed_count']}")
        print(f"Unchanged families: {summary['unchanged_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "venue-lag-watchlist":
        report = write_venue_lag_watchlist_report(
            args.input,
            args.json_output,
            args.markdown_output,
            stale_seconds=args.stale_seconds,
            price_delta_threshold=args.price_delta_threshold,
        )
        print("Mode: saved venue lag watchlist")
        print(f"Watchlist rows: {report['watchlist_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "import-llm-hypotheses":
        if args.snapshots_dir or args.snapshot_file:
            snapshot, _, mode = _load_snapshot_mode(args)
        else:
            snapshot, _, mode = _load_fixture_mode(args.real_quote_fixtures_dir)
        report = write_imported_llm_relationship_hypotheses_report(snapshot, args.input, args.json_output)
        print(f"Mode: saved LLM relationship hypothesis import ({mode})")
        print(f"Validated hypotheses: {report['hypothesis_count']}")
        print(f"Rejected hypotheses: {report['rejected_hypothesis_count']}")
        print(f"Wrote {args.json_output}")
        return 0
    if args.command == "write-signal-persistence-report":
        report = write_signal_persistence_report(
            args.current,
            args.previous,
            args.json_output,
            args.markdown_output,
        )
        summary = report["summary"]
        print("Mode: saved signal persistence report")
        print(f"Current signals: {summary['total_current']}")
        print(f"New signals: {summary['new_count']}")
        print(f"Persistent signals: {summary['persistent_count']}")
        print(f"Worsened signals: {summary['worsened_count']}")
        print(f"Improved signals: {summary['improved_count']}")
        print(f"Resolved signals: {summary['resolved_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "write-graph-rv-investigation-packets":
        report = write_graph_to_relative_value_investigation_packets_report(
            trade_indicator_path=args.trade_indicators,
            probability_constraints_path=args.probability_constraints,
            llm_hypotheses_path=args.llm_hypotheses,
            signal_persistence_path=args.signal_persistence,
            event_entity_ontology_path=args.event_entity_ontology,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        print("Mode: saved graph-to-relative-value investigation packets")
        print(f"Packets: {report['packet_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "write-platform-expansion-radar":
        report = write_platform_expansion_radar_report(
            trade_indicators_path=args.trade_indicators,
            probability_constraints_path=args.probability_constraints,
            rv_investigation_packets_path=args.rv_investigation_packets,
            state_family_registry_path=args.state_family_registry,
            signal_persistence_path=args.signal_persistence,
            event_entity_ontology_path=args.event_entity_ontology,
            relative_value_reports_dir=args.relative_value_reports_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        print("Mode: saved platform expansion radar")
        print(f"Gap rows: {len(report['platform_gap_rows'])}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "write-family-inference-audit":
        audit_input = {
            "trade_indicator_report": _load_saved_json_report(args.trade_indicators),
            "probability_constraints_report": _load_saved_json_report(args.probability_constraints),
            "rv_investigation_packets_report": _load_saved_json_report(args.rv_investigation_packets),
        }
        report = write_family_inference_audit_report(audit_input, args.json_output)
        print("Mode: saved family inference audit")
        print(f"Rows: {report['row_count']}")
        print(f"Wrote {args.json_output}")
        return 0
    if args.command == "write-event-entity-ontology":
        if args.snapshots_dir or args.snapshot_file:
            snapshot, _, mode = _load_snapshot_mode(args)
        else:
            snapshot, _, mode = _load_fixture_mode(args.real_quote_fixtures_dir)
        report = write_event_entity_ontology_report(
            snapshot,
            args.json_output,
            args.markdown_output,
            llm_hypotheses_report=_load_existing_llm_hypotheses_report(args.llm_hypotheses),
        )
        print(f"Mode: saved event/entity ontology ({mode})")
        print(f"Entities: {report['entity_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "ingest-relative-value-diagnostics":
        report = write_rv_diagnostic_relationship_edges_report(
            rv_reports_dir=args.rv_reports_dir,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print("Mode: saved RV diagnostic relationship ingest")
        print(f"Total nodes: {summary['total_nodes']}")
        print(f"Total edges: {summary['total_edges']}")
        top_types = summary["edges_by_relationship_type"][:5]
        if top_types:
            print("Top relationship types:")
            for entry in top_types:
                print(f"  - {entry['relationship_type']}: {entry['count']}")
        top_blockers = summary["top_blockers"][:5]
        if top_blockers:
            print("Top blockers:")
            for entry in top_blockers:
                print(f"  - {entry['blocker']}: {entry['count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "export-rv-review-worklist":
        report = write_rv_review_worklist_report(
            edges_report_path=args.edges,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            include_queued_ibkr=args.include_queued_ibkr,
        )
        summary = report["summary"]
        print("Mode: saved RV review worklist")
        print(f"Worklist rows: {summary['total_rows']}")
        for entry in summary["rows_by_action"]:
            print(f"  - {entry['allowed_next_action']}: {entry['count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "llm-review-graph-relationships":
        result = write_llm_graph_relationship_review_assets(
            edges_report_path=args.input,
            prompt_output=args.prompt_output,
            schema_output=args.expected_json_schema_output,
            sample_size=args.sample_size,
        )
        print("Mode: saved LLM graph relationship review prompt/schema")
        print(f"Sample size: {result['sample_size_actual']} of {result['sample_size_requested']} requested")
        print(f"Wrote {args.prompt_output}")
        print(f"Wrote {args.expected_json_schema_output}")
        return 0
    if args.command == "validate-llm-graph-relationship-review":
        report = validate_llm_graph_relationship_review_output(
            output_path=args.input,
            schema_path=args.schema,
            edges_report_path=args.edges,
            json_output=args.json_output,
        )
        summary = report["summary"]
        print("Mode: saved LLM graph relationship review validation")
        print(f"Status: {report['validation_status']}")
        print(f"Accepted rows: {summary['accepted_count']}")
        print(f"Rejected rows: {summary['rejected_count']}")
        print(f"Structural errors: {summary['structural_error_count']}")
        print(f"Wrote {args.json_output}")
        return 0
    if args.command == "graph-manual-relationship-evidence":
        report = write_graph_manual_relationship_evidence_report(
            rv_reports_dir=args.rv_reports_dir,
            edges_report_path=args.edges,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print("Mode: saved graph manual relationship evidence")
        print(f"Total records: {summary['total_records']}")
        print(f"Ready for RV now: {summary['ready_for_rv_now']}")
        print(f"Blocked on manual evidence: {summary['blocked_on_manual_evidence']}")
        print("By vertical:")
        for entry in summary["records_by_vertical"]:
            print(f"  - {entry['vertical']}: {entry['count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "graph-manual-discovery-backlog":
        report = write_graph_manual_discovery_backlog_report(
            relationships_path=args.relationships,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
        summary = report["summary"]
        print("Mode: saved graph manual discovery backlog")
        print(f"Total backlog items: {summary['total_items']}")
        print(f"HIGH urgency: {summary['by_urgency'].get('HIGH', 0)}")
        print(f"MEDIUM urgency: {summary['by_urgency'].get('MEDIUM', 0)}")
        print(f"LOW urgency: {summary['by_urgency'].get('LOW', 0)}")
        print("By vertical:")
        for entry in summary["by_vertical"]:
            print(f"  - {entry['vertical']}: {entry['count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0
    if args.command == "llm-review-graph-manual-evidence":
        result = write_llm_graph_manual_evidence_review_assets(
            relationships_path=args.input,
            prompt_output=args.prompt_output,
            schema_output=args.expected_json_schema_output,
            sample_size=args.sample_size,
        )
        print("Mode: saved LLM graph manual-evidence prompt/schema")
        print(f"Sample size: {result['sample_size_actual']} of {result['sample_size_requested']} requested")
        print(f"Wrote {args.prompt_output}")
        print(f"Wrote {args.expected_json_schema_output}")
        return 0
    if args.command == "validate-llm-graph-manual-evidence-review":
        report = validate_llm_graph_manual_evidence_review_output(
            output_path=args.input,
            schema_path=args.schema,
            relationships_path=args.relationships,
            json_output=args.json_output,
        )
        summary = report["summary"]
        print("Mode: saved LLM graph manual-evidence validation")
        print(f"Status: {report['validation_status']}")
        print(f"Accepted rows: {summary['accepted_count']}")
        print(f"Rejected rows: {summary['rejected_count']}")
        print(f"Structural errors: {summary['structural_error_count']}")
        print(f"Wrote {args.json_output}")
        return 0
    if args.command == "write-stale-lag-watchlist":
        if args.snapshots_dir or args.snapshot_file:
            snapshot, _, mode = _load_snapshot_mode(args)
        else:
            snapshot, _, mode = _load_fixture_mode(args.real_quote_fixtures_dir)
        report = write_stale_lag_watchlist_report(
            snapshot,
            args.json_output,
            args.markdown_output,
            llm_hypotheses_report=_load_existing_llm_hypotheses_report(args.llm_hypotheses),
        )
        print(f"Mode: saved stale/lag watchlist ({mode})")
        print(f"Watch rows: {report['stale_lag_watch_count']}")
        print(f"Blocked rows: {report['stale_lag_blocked_count']}")
        print(f"Wrote {args.json_output}")
        print(f"Wrote {args.markdown_output}")
        return 0

    if args.snapshots_dir or args.snapshot_file:
        snapshot, source_metadata, mode = _load_snapshot_mode(args)
    else:
        snapshot, source_metadata, mode = _load_fixture_mode(args.real_quote_fixtures_dir)

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
    venue_native_groups_json_path = REPORTS_DIR / "venue_native_exhaustive_groups.json"
    venue_native_groups_md_path = REPORTS_DIR / "venue_native_exhaustive_groups.md"
    bounded_noarb_json_path = REPORTS_DIR / "bounded_noarb_consistency.json"
    bounded_noarb_md_path = REPORTS_DIR / "bounded_noarb_consistency.md"
    payoff_state_json_path = REPORTS_DIR / "market_graph_payoff_state_diagnostics.json"
    payoff_state_md_path = REPORTS_DIR / "market_graph_payoff_state_diagnostics.md"
    payoff_state_bridge_json_path = REPORTS_DIR / "market_graph_payoff_state_feasibility_bridge.json"
    state_family_registry_json_path = REPORTS_DIR / "market_graph_state_family_registry.json"
    state_family_registry_md_path = REPORTS_DIR / "market_graph_state_family_registry.md"
    probability_constraints_json_path = REPORTS_DIR / "market_graph_probability_constraints.json"
    trade_indicators_json_path = REPORTS_DIR / "market_graph_trade_indicators.json"
    trade_indicators_csv_path = REPORTS_DIR / "market_graph_trade_indicators.csv"
    signal_persistence_json_path = REPORTS_DIR / "market_graph_signal_persistence.json"
    signal_persistence_md_path = REPORTS_DIR / "market_graph_signal_persistence.md"
    graph_to_rv_packets_json_path = REPORTS_DIR / "graph_to_relative_value_investigation_packets.json"
    graph_to_rv_packets_md_path = REPORTS_DIR / "graph_to_relative_value_investigation_packets.md"
    ops_status_json_path = REPORTS_DIR / "market_graph_ops_status.json"
    ops_status_md_path = REPORTS_DIR / "market_graph_ops_status.md"
    platform_expansion_radar_json_path = REPORTS_DIR / "market_graph_platform_expansion_radar.json"
    platform_expansion_radar_md_path = REPORTS_DIR / "market_graph_platform_expansion_radar.md"
    event_entity_ontology_json_path = REPORTS_DIR / "market_graph_event_entity_ontology.json"
    event_entity_ontology_md_path = REPORTS_DIR / "market_graph_event_entity_ontology.md"
    stale_lag_watchlist_json_path = REPORTS_DIR / "market_graph_stale_lag_watchlist.json"
    stale_lag_watchlist_md_path = REPORTS_DIR / "market_graph_stale_lag_watchlist.md"
    saved_quote_overlay_status_json_path = REPORTS_DIR / "market_graph_saved_quote_overlay_status.json"
    saved_quote_overlay_status_md_path = REPORTS_DIR / "market_graph_saved_quote_overlay_status.md"
    previous_trade_indicators_json_path = REPORTS_DIR / "previous_market_graph_trade_indicators.json"
    previous_probability_constraints_json_path = REPORTS_DIR / "previous_market_graph_probability_constraints.json"
    previous_payoff_state_bridge_json_path = REPORTS_DIR / "previous_market_graph_payoff_state_feasibility_bridge.json"
    llm_packets_path = REPORTS_DIR / "llm_relationship_review_packets.jsonl"
    llm_hypotheses_path = REPORTS_DIR / "llm_relationship_hypotheses_validated.json"

    # Snapshot the prior diagnostic reports before they are overwritten so the
    # downstream signal-persistence diff has a true previous-run baseline. Only
    # the inputs the persistence module reads need to be preserved; everything
    # else is rebuilt from fresh fixtures every run.
    for current, previous in (
        (trade_indicators_json_path, previous_trade_indicators_json_path),
        (probability_constraints_json_path, previous_probability_constraints_json_path),
        (payoff_state_bridge_json_path, previous_payoff_state_bridge_json_path),
    ):
        if current.exists():
            previous.write_bytes(current.read_bytes())

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
    write_venue_native_exhaustive_groups_report(
        snapshot,
        source_metadata,
        venue_native_groups_json_path,
        venue_native_groups_md_path,
    )
    write_bounded_noarb_report(snapshot, bounded_noarb_json_path, bounded_noarb_md_path)
    write_payoff_state_diagnostics_report(snapshot, payoff_state_json_path, payoff_state_md_path)
    write_payoff_state_feasibility_bridge_report(snapshot, payoff_state_bridge_json_path)
    write_state_family_registry_report(state_family_registry_json_path, state_family_registry_md_path)
    write_probability_constraints_report(snapshot, probability_constraints_json_path)
    write_llm_relationship_review_packets(snapshot, llm_packets_path)
    saved_llm_hypotheses_report = _load_existing_llm_hypotheses_report(llm_hypotheses_path)
    if saved_llm_hypotheses_report is None:
        advisory_report = write_llm_relationship_hypotheses_report(snapshot, llm_hypotheses_path)
    else:
        advisory_report = saved_llm_hypotheses_report
    event_entity_ontology_report = write_event_entity_ontology_report(
        snapshot,
        event_entity_ontology_json_path,
        event_entity_ontology_md_path,
        llm_hypotheses_report=advisory_report,
    )
    stale_lag_report = write_stale_lag_watchlist_report(
        snapshot,
        stale_lag_watchlist_json_path,
        stale_lag_watchlist_md_path,
        llm_hypotheses_report=advisory_report,
    )
    write_trade_indicator_report(
        snapshot,
        trade_indicators_json_path,
        trade_indicators_csv_path,
        violations,
        llm_hypotheses_report=advisory_report,
    )
    write_signal_persistence_report(
        [trade_indicators_json_path, probability_constraints_json_path, payoff_state_bridge_json_path],
        [
            previous_trade_indicators_json_path,
            previous_probability_constraints_json_path,
            previous_payoff_state_bridge_json_path,
        ],
        signal_persistence_json_path,
        signal_persistence_md_path,
        previous_persistence_path=signal_persistence_json_path,
    )
    rv_packets_report = write_graph_to_relative_value_investigation_packets_report(
        trade_indicator_path=trade_indicators_json_path,
        probability_constraints_path=probability_constraints_json_path,
        llm_hypotheses_path=llm_hypotheses_path,
        signal_persistence_path=signal_persistence_json_path,
        event_entity_ontology_report=event_entity_ontology_report,
        json_output=graph_to_rv_packets_json_path,
        markdown_output=graph_to_rv_packets_md_path,
        max_packets=100,
    )
    # Platform expansion radar and ontology must finish before ops_status so the
    # daily operator surface can read fresh gap counts and entity coverage
    # without surfacing stale cached values.
    write_platform_expansion_radar_report(
        trade_indicators_path=trade_indicators_json_path,
        probability_constraints_path=probability_constraints_json_path,
        rv_investigation_packets_path=graph_to_rv_packets_json_path,
        state_family_registry_path=state_family_registry_json_path,
        signal_persistence_path=signal_persistence_json_path,
        event_entity_ontology_report=event_entity_ontology_report,
        json_output=platform_expansion_radar_json_path,
        markdown_output=platform_expansion_radar_md_path,
    )
    rv_diagnostic_edges_json_path = REPORTS_DIR / "rv_diagnostic_relationship_edges.json"
    rv_review_worklist_json_path = REPORTS_DIR / "rv_review_worklist.json"
    llm_graph_prompt_path = REPORTS_DIR / "llm_graph_relationship_review_prompt.md"
    llm_graph_schema_path = REPORTS_DIR / "llm_graph_relationship_review_schema.json"
    graph_manual_evidence_json_path = REPORTS_DIR / "graph_manual_relationship_evidence.json"
    graph_manual_backlog_json_path = REPORTS_DIR / "graph_manual_discovery_backlog.json"
    llm_graph_manual_prompt_path = REPORTS_DIR / "llm_graph_manual_evidence_prompt.md"
    llm_graph_manual_schema_path = REPORTS_DIR / "llm_graph_manual_evidence_schema.json"
    write_market_graph_ops_status_report(
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of.isoformat(),
        trade_indicators_path=trade_indicators_json_path,
        probability_constraints_path=probability_constraints_json_path,
        payoff_state_feasibility_bridge_path=payoff_state_bridge_json_path,
        signal_persistence_path=signal_persistence_json_path,
        rv_investigation_packets_path=graph_to_rv_packets_json_path,
        stale_lag_watchlist_path=stale_lag_watchlist_json_path,
        platform_expansion_radar_path=platform_expansion_radar_json_path,
        event_entity_ontology_path=event_entity_ontology_json_path,
        rv_diagnostic_relationship_edges_path=rv_diagnostic_edges_json_path,
        rv_review_worklist_path=rv_review_worklist_json_path,
        llm_graph_relationship_review_prompt_path=llm_graph_prompt_path,
        llm_graph_relationship_review_schema_path=llm_graph_schema_path,
        graph_manual_relationship_evidence_path=graph_manual_evidence_json_path,
        graph_manual_discovery_backlog_path=graph_manual_backlog_json_path,
        llm_graph_manual_evidence_prompt_path=llm_graph_manual_prompt_path,
        llm_graph_manual_evidence_schema_path=llm_graph_manual_schema_path,
        json_output=ops_status_json_path,
        markdown_output=ops_status_md_path,
    )
    if args.real_quote_fixtures_dir is not None and mode == "fixtures_with_real_quote_fixtures":
        baseline_snapshot, _, _ = _load_fixture_mode()
        baseline_stale_lag_report = build_stale_lag_watchlist_report(
            baseline_snapshot,
            llm_hypotheses_report=advisory_report,
        )
        write_saved_quote_overlay_status_report(
            json_output=saved_quote_overlay_status_json_path,
            markdown_output=saved_quote_overlay_status_md_path,
            overlay_metadata=_overlay_metadata(source_metadata),
            before_stale_lag_report=baseline_stale_lag_report,
            after_stale_lag_report=stale_lag_report,
            rv_packets_report=rv_packets_report,
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
    print(f"Wrote {venue_native_groups_json_path}")
    print(f"Wrote {venue_native_groups_md_path}")
    print(f"Wrote {bounded_noarb_json_path}")
    print(f"Wrote {bounded_noarb_md_path}")
    print(f"Wrote {payoff_state_json_path}")
    print(f"Wrote {payoff_state_md_path}")
    print(f"Wrote {payoff_state_bridge_json_path}")
    print(f"Wrote {state_family_registry_json_path}")
    print(f"Wrote {state_family_registry_md_path}")
    print(f"Wrote {probability_constraints_json_path}")
    print(f"Wrote {trade_indicators_json_path}")
    print(f"Wrote {trade_indicators_csv_path}")
    print(f"Wrote {signal_persistence_json_path}")
    print(f"Wrote {signal_persistence_md_path}")
    print(f"Wrote {graph_to_rv_packets_json_path}")
    print(f"Wrote {graph_to_rv_packets_md_path}")
    print(f"Wrote {ops_status_json_path}")
    print(f"Wrote {ops_status_md_path}")
    print(f"Wrote {platform_expansion_radar_json_path}")
    print(f"Wrote {platform_expansion_radar_md_path}")
    print(f"Wrote {event_entity_ontology_json_path}")
    print(f"Wrote {event_entity_ontology_md_path}")
    print(f"Wrote {stale_lag_watchlist_json_path}")
    print(f"Wrote {stale_lag_watchlist_md_path}")
    if args.real_quote_fixtures_dir is not None and mode == "fixtures_with_real_quote_fixtures":
        print(f"Wrote {saved_quote_overlay_status_json_path}")
        print(f"Wrote {saved_quote_overlay_status_md_path}")
    print(f"Wrote {llm_packets_path}")
    print(f"Wrote {llm_hypotheses_path}")
    return 0


def _overlay_metadata(source_metadata: list[dict]) -> dict | None:
    for item in reversed(source_metadata):
        if item.get("file") == "real_quote_fixture_overlay":
            return item
    return None


if __name__ == "__main__":
    raise SystemExit(main())
