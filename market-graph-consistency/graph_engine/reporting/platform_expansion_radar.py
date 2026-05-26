from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


BANNER = (
    "Saved-file-only platform expansion radar. Rows identify data gaps for diagnostic review "
    "and do not affect evaluator gates."
)
WHY_REVIEW_ONLY_YET = (
    "Expansion radar only; data gaps require saved snapshots, settlement/source review, grouping proof, "
    "depth/freshness review, fee review, and eligibility review before any downstream RV use."
)
EXPECTED_VALUES = {"HIGH", "MEDIUM", "LOW"}
ALLOWED_NEXT_ACTIONS = {
    "FETCH_SAVED_MARKET_SNAPSHOT",
    "BUILD_READ_ONLY_ADAPTER",
    "BUILD_FIXTURE_FIRST",
    "MANUAL_PLATFORM_REVIEW",
    "IGNORE_LOW_VALUE",
}
SIGNAL_SOURCE_KEYS = {
    "trade_indicator",
    "probability_constraint",
    "rv_handoff_packet",
    "state_family",
}
REQUIRED_FIELDS_TO_FETCH = [
    "market_identity",
    "event_grouping",
    "outcomes_selections",
    "settlement_rules_source",
    "close_resolution_time",
    "orderbook_depth_freshness",
    "fee_commission",
    "region_account_eligibility_if_relevant",
]
REQUIRED_FETCH_FIELD_SET = set(REQUIRED_FIELDS_TO_FETCH)
REFERENCE_ONLY_VENUES = {
    "the_odds_api",
    "odds_api",
}


@dataclass
class _RadarContext:
    family_to_venues: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    family_sources: dict[str, dict[str, bool]] = field(default_factory=dict)
    family_scores: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    family_high_confidence: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    family_persistence: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    family_ontology_priority_scores: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    family_ontology_priority_reasons: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    ontology_report_used: bool = False
    venues_seen: set[str] = field(default_factory=set)
    rv_profile_venues: set[str] = field(default_factory=set)
    rv_auth_required_venues: set[str] = field(default_factory=set)
    rv_profile_only_venues: set[str] = field(default_factory=set)
    rv_reference_only_venues: set[str] = field(default_factory=set)
    rv_reference_only_venue_families: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_family(
        self,
        family: str,
        *,
        venues: list[str] | None = None,
        source: str | None = None,
        score: float = 0.0,
        confidence: str | None = None,
        persistence_count: int = 0,
    ) -> None:
        family = _normalise_family(family)
        if not family:
            return
        self.family_sources.setdefault(family, {key: False for key in SIGNAL_SOURCE_KEYS})
        if source in SIGNAL_SOURCE_KEYS:
            self.family_sources[family][source] = True
        for venue in venues or []:
            normalized = _normalise_venue(venue)
            if normalized:
                self.family_to_venues[family].add(normalized)
                self.venues_seen.add(normalized)
        self.family_scores[family] = max(self.family_scores[family], score)
        if confidence == "HIGH":
            self.family_high_confidence[family] += 1
        if persistence_count:
            self.family_persistence[family] += persistence_count

    def family_present(self, family: str) -> bool:
        return _normalise_family(family) in self.family_sources

    def venues_for(self, family: str) -> list[str]:
        return sorted(self.family_to_venues.get(_normalise_family(family), set()))

    def sources_for(self, family: str) -> dict[str, bool]:
        return dict(self.family_sources.get(_normalise_family(family), {key: False for key in SIGNAL_SOURCE_KEYS}))

    def add_ontology_priority(self, family: str, reason: str) -> None:
        family = _normalise_family(family)
        if not family:
            return
        self.family_ontology_priority_scores[family] += 1
        self.family_ontology_priority_reasons[family].add(reason)

    def ontology_priority_score(self, family: str) -> int:
        return int(self.family_ontology_priority_scores.get(_normalise_family(family), 0))

    def ontology_priority_reasons(self, family: str) -> list[str]:
        return sorted(self.family_ontology_priority_reasons.get(_normalise_family(family), set()))


def build_platform_expansion_radar_report(
    *,
    trade_indicator_report: dict[str, Any] | None = None,
    probability_constraints_report: dict[str, Any] | None = None,
    rv_investigation_packets_report: dict[str, Any] | None = None,
    state_family_registry_report: dict[str, Any] | None = None,
    signal_persistence_report: dict[str, Any] | None = None,
    event_entity_ontology_report: dict[str, Any] | None = None,
    relative_value_reports: list[dict[str, Any]] | None = None,
    input_blockers: list[str] | None = None,
) -> dict[str, Any]:
    context = _collect_context(
        trade_indicator_report=trade_indicator_report,
        probability_constraints_report=probability_constraints_report,
        rv_investigation_packets_report=rv_investigation_packets_report,
        state_family_registry_report=state_family_registry_report,
        signal_persistence_report=signal_persistence_report,
        event_entity_ontology_report=event_entity_ontology_report,
        relative_value_reports=relative_value_reports or [],
    )
    blockers = sorted(set(input_blockers or []) | set(_content_blockers(context)))
    gap_rows = _build_gap_rows(context)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": BANNER,
        "families_seen": sorted(context.family_sources),
        "venues_seen": sorted(context.venues_seen | context.rv_profile_venues),
        "ontology_report_used": context.ontology_report_used,
        "platform_gap_rows": gap_rows,
        "recommended_platform_fetches": _recommended_platform_fetches(gap_rows),
        "recommended_family_fetches": _recommended_family_fetches(gap_rows),
        "blockers": blockers,
        "why_review_only_yet": WHY_REVIEW_ONLY_YET,
    }
    validate_platform_expansion_radar_report(report)
    return report


def write_platform_expansion_radar_report(
    *,
    json_output: Path | str,
    markdown_output: Path | str,
    trade_indicators_path: Path | str | None = None,
    probability_constraints_path: Path | str | None = None,
    rv_investigation_packets_path: Path | str | None = None,
    state_family_registry_path: Path | str | None = None,
    signal_persistence_path: Path | str | None = None,
    event_entity_ontology_path: Path | str | None = None,
    event_entity_ontology_report: dict[str, Any] | None = None,
    relative_value_reports_dir: Path | str | None = None,
) -> dict[str, Any]:
    input_reports = {
        "trade_indicators": _load_optional_report(trade_indicators_path),
        "probability_constraints": _load_optional_report(probability_constraints_path),
        "rv_investigation_packets": _load_optional_report(rv_investigation_packets_path),
        "state_family_registry": _load_optional_report(state_family_registry_path),
        "signal_persistence": _load_optional_report(signal_persistence_path),
    }
    blockers = [
        f"missing_input_report:{name}"
        for name, payload in input_reports.items()
        if payload is None
    ]
    loaded_ontology_report = event_entity_ontology_report
    if loaded_ontology_report is None:
        loaded_ontology_report = _load_optional_report(event_entity_ontology_path)
    if event_entity_ontology_path is not None and loaded_ontology_report is None:
        blockers.append("missing_event_entity_ontology_report")
    rv_reports, rv_blockers = _load_relative_value_reports_dir(relative_value_reports_dir)
    blockers.extend(rv_blockers)
    report = build_platform_expansion_radar_report(
        trade_indicator_report=input_reports["trade_indicators"],
        probability_constraints_report=input_reports["probability_constraints"],
        rv_investigation_packets_report=input_reports["rv_investigation_packets"],
        state_family_registry_report=input_reports["state_family_registry"],
        signal_persistence_report=input_reports["signal_persistence"],
        event_entity_ontology_report=loaded_ontology_report,
        relative_value_reports=rv_reports,
        input_blockers=blockers,
    )
    markdown = render_platform_expansion_radar_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "platform expansion radar Markdown contains prohibited vocabulary: " + ", ".join(findings)
        )

    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def write_family_inference_audit_report(report: dict[str, Any], json_output: Path | str) -> dict[str, Any]:
    rows = _family_inference_audit_rows(report)
    audit = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "row_count": len(rows),
        "family_inference_audit": rows,
    }
    path = Path(json_output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit


def validate_platform_expansion_radar_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("platform expansion radar must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("platform expansion radar must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("platform expansion radar actions must be WATCH and MANUAL_REVIEW only")
    if not isinstance(report.get("ontology_report_used"), bool):
        raise SchemaValidationError("ontology_report_used must be a boolean")
    for key in [
        "families_seen",
        "venues_seen",
        "platform_gap_rows",
        "recommended_platform_fetches",
        "recommended_family_fetches",
        "blockers",
    ]:
        if not isinstance(report.get(key), list):
            raise SchemaValidationError(f"{key} must be a list")
    if not isinstance(report.get("why_review_only_yet"), str) or not report["why_review_only_yet"]:
        raise SchemaValidationError("why_review_only_yet must be a non-empty string")
    for index, row in enumerate(report["platform_gap_rows"]):
        _validate_gap_row(row, f"platform_gap_rows[{index}]")
    for index, row in enumerate(report["recommended_platform_fetches"]):
        _validate_recommendation(row, f"recommended_platform_fetches[{index}]", "missing_platform_or_venue")
    for index, row in enumerate(report["recommended_family_fetches"]):
        _validate_recommendation(row, f"recommended_family_fetches[{index}]", "family")


def render_platform_expansion_radar_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Market Graph Platform Expansion Radar",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Families seen: `{', '.join(report['families_seen']) or 'none'}`",
        f"- Venues seen: `{', '.join(report['venues_seen']) or 'none'}`",
        f"- Ontology report used: `{str(report.get('ontology_report_used', False)).lower()}`",
        "",
        "## Recommended Platform Fetches",
        "",
    ]
    if report["recommended_platform_fetches"]:
        lines.extend(
            f"- `{row['missing_platform_or_venue']}`: `{row['expected_value_of_fetch']}` via `{row['allowed_next_action']}` "
            f"(ontology priority `{row['ontology_priority_score']}`)"
            for row in report["recommended_platform_fetches"]
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Recommended Family Fetches", ""])
    if report["recommended_family_fetches"]:
        lines.extend(
            f"- `{row['family']}`: `{row['expected_value_of_fetch']}` via `{row['allowed_next_action']}` "
            f"(ontology priority `{row['ontology_priority_score']}`)"
            for row in report["recommended_family_fetches"]
        )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Gap Rows",
            "",
            "| Family | Missing venue | Existing venues | Fetch value | Next action | Reason | Risks |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if not report["platform_gap_rows"]:
        lines.append("| none |  |  |  |  |  |  |")
    for row in report["platform_gap_rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["family"]),
                    _md(row["missing_platform_or_venue"]),
                    _md(", ".join(row["existing_venues"])),
                    _md(row["expected_value_of_fetch"]),
                    _md(row["allowed_next_action"]),
                    _md(row["opportunity_reason"]),
                    _md(", ".join(row["fake_edge_risks"])),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        lines.extend(f"- `{blocker}`" for blocker in report["blockers"])
    else:
        lines.append("- none")
    lines.extend(["", "## Why Review Only", "", report["why_review_only_yet"], ""])
    return "\n".join(lines)


def _collect_context(
    *,
    trade_indicator_report: dict[str, Any] | None,
    probability_constraints_report: dict[str, Any] | None,
    rv_investigation_packets_report: dict[str, Any] | None,
    state_family_registry_report: dict[str, Any] | None,
    signal_persistence_report: dict[str, Any] | None,
    event_entity_ontology_report: dict[str, Any] | None,
    relative_value_reports: list[dict[str, Any]],
) -> _RadarContext:
    context = _RadarContext()
    for row in _list_from_report(trade_indicator_report, "signals"):
        family = _infer_family(row)
        context.add_family(
            family,
            venues=_row_venues(row),
            source="trade_indicator",
            score=_number(row.get("severity_score")),
            confidence=_optional_string(row.get("confidence_tier")),
        )
    for row in _list_from_report(probability_constraints_report, "probability_constraints"):
        family = _infer_family(row)
        context.add_family(
            family,
            venues=_row_venues(row),
            source="probability_constraint",
            score=_number(row.get("severity_score")),
            confidence=_optional_string(row.get("confidence_tier")),
        )
    for row in _list_from_report(rv_investigation_packets_report, "investigation_packets"):
        family = _infer_family(row)
        context.add_family(
            family,
            venues=_row_venues(row),
            source="rv_handoff_packet",
            score=_number(row.get("priority_score")),
            confidence=_optional_string(row.get("confidence_tier")),
            persistence_count=_int(row.get("persistence_count")),
        )
    for row in _list_from_report(state_family_registry_report, "state_family_registry_entries"):
        context.add_family(
            str(row.get("formula_family") or ""),
            source="state_family",
            score=40 if row.get("is_finite_state_safe") is True else 10,
        )
    for row in _list_from_report(signal_persistence_report, "signal_persistence_rows"):
        family = _infer_family(row)
        context.add_family(
            family,
            venues=_row_venues(row),
            source=None,
            score=_number(row.get("current_severity")),
            confidence=_optional_string(row.get("current_confidence")),
            persistence_count=_int(row.get("persistence_count")),
        )
    for payload in relative_value_reports:
        _collect_relative_value_payload_context(context, payload)
    _apply_ontology_priorities(context, event_entity_ontology_report)
    return context


def _apply_ontology_priorities(
    context: _RadarContext,
    event_entity_ontology_report: dict[str, Any] | None,
) -> None:
    if not isinstance(event_entity_ontology_report, dict):
        return
    context.ontology_report_used = True
    summary = event_entity_ontology_report.get("summary")
    cross_venue_candidates: set[str] = set()
    if isinstance(summary, dict):
        cross_venue_candidates = {
            item
            for item in summary.get("cross_venue_entity_candidates", [])
            if isinstance(item, str)
        }
    for row in _list_from_report(event_entity_ontology_report, "ontology_rows"):
        family = _ontology_row_family(row)
        if not family:
            continue
        entity_id = _first_text(row, ["entity_id"])
        if entity_id and entity_id in cross_venue_candidates:
            context.add_ontology_priority(family, "cross_venue_entity_candidate")
        if row.get("confidence_tier") == "HIGH":
            context.add_ontology_priority(family, "high_confidence_entity")
        if _int(row.get("persistence_count")) > 1:
            context.add_ontology_priority(family, "persistent_entity")


def _ontology_row_family(row: dict[str, Any]) -> str:
    explicit = _first_text(row, ["family", "formula_family", "market_family", "event_family"])
    if explicit:
        return _normalise_family(explicit)
    entity_type = _first_text(row, ["entity_type"])
    text = _row_text(row)
    if entity_type in {"CRYPTO_ASSET", "CRYPTO_THRESHOLD_EVENT"}:
        return "BTC_THRESHOLD" if _word(text, "btc") or _word(text, "bitcoin") else ""
    if entity_type == "FED_MEETING":
        return "FED_MEETING_RANGE"
    if entity_type == "SPORTS_GAME":
        return "SPORTS_GAME_LEVEL"
    if entity_type in {"SPORTS_TEAM", "SPORTS_CHAMPIONSHIP"}:
        return "SPORTS_CHAMPION"
    if entity_type == "WEATHER_STATION":
        return "WEATHER_RANGE"
    if entity_type in {"ELECTION_CONTEST", "CANDIDATE_OR_PARTY"}:
        return "ELECTION_OUTCOME"
    inferred = _infer_family(row)
    return "" if inferred == "UNKNOWN" else inferred


def _collect_relative_value_payload_context(context: _RadarContext, payload: dict[str, Any]) -> None:
    for row in _iter_candidate_rows(payload):
        venue = _normalise_venue(_first_text(row, ["venue", "platform", "exchange", "missing_platform_or_venue"]))
        family = _infer_family(row)
        if venue:
            context.rv_profile_venues.add(venue)
            context.venues_seen.add(venue)
            context.add_family(family or "UNKNOWN", venues=[venue])
            if _row_is_reference_only(row, venue):
                context.rv_reference_only_venues.add(venue)
                context.rv_reference_only_venue_families[venue].add(_normalise_family(family or "PLATFORM_PROFILE"))
            if _row_requires_auth_review(row):
                context.rv_auth_required_venues.add(venue)
            if _row_is_profile_only(row):
                context.rv_profile_only_venues.add(venue)
        if family:
            context.add_family(family, venues=[venue] if venue else None)


def _build_gap_rows(context: _RadarContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if context.family_present("BTC_THRESHOLD") and "polymarket" not in context.venues_for("BTC_THRESHOLD"):
        rows.append(
            _gap_row(
                context,
                family="BTC_THRESHOLD",
                missing_platform_or_venue="polymarket",
                opportunity_reason=(
                    "BTC threshold diagnostics are present; Polymarket saved snapshots would improve cross-platform "
                    "relationship discovery for the same typed family."
                ),
                fake_edge_risks=["settlement_basis_mismatch", "missing_group_metadata", "stale_snapshot"],
                allowed_next_action="FETCH_SAVED_MARKET_SNAPSHOT",
                expected_value=_family_expected_value(context, "BTC_THRESHOLD"),
            )
        )
    if context.family_present("FED_MEETING_RANGE") and "polymarket" not in context.venues_for("FED_MEETING_RANGE"):
        rows.append(
            _gap_row(
                context,
                family="FED_MEETING_RANGE",
                missing_platform_or_venue="polymarket",
                opportunity_reason=(
                    "Fed target-range diagnostics are present; Polymarket saved snapshots would improve source/date "
                    "and bucket comparison coverage."
                ),
                fake_edge_risks=["settlement_basis_mismatch", "date_window_mismatch", "bucket_boundary_mismatch"],
                allowed_next_action="FETCH_SAVED_MARKET_SNAPSHOT",
                expected_value=_family_expected_value(context, "FED_MEETING_RANGE"),
            )
        )
    if "crypto_com_predict_cdna" in context.rv_profile_venues:
        family = "BTC_THRESHOLD" if context.family_present("BTC_THRESHOLD") else "CRYPTO_PLATFORM_PROFILE"
        rows.append(
            _gap_row(
                context,
                family=family,
                missing_platform_or_venue="crypto_com_predict_cdna",
                opportunity_reason=(
                    "Crypto.com Predict/CDNA appears as saved profile data; use a fixture-backed read-only adapter "
                    "before transport scoping."
                ),
                fake_edge_risks=["platform_profile_only", "settlement_basis_mismatch", "region_eligibility_unknown"],
                allowed_next_action="BUILD_FIXTURE_FIRST",
                expected_value="HIGH" if context.family_present("BTC_THRESHOLD") else "MEDIUM",
            )
        )
    for venue in sorted(context.rv_reference_only_venues):
        families = sorted(
            family
            for family in context.rv_reference_only_venue_families.get(venue, {"PLATFORM_PROFILE"})
            if family
        )
        for family in families or ["PLATFORM_PROFILE"]:
            rows.append(
                _gap_row(
                    context,
                    family=family,
                    missing_platform_or_venue=venue,
                    opportunity_reason=(
                        "fair_value_reference_only_not_executable_leg: saved reference feed context may inform "
                        "manual fair-value review, but it is not a platform leg for graph handoff."
                    ),
                    fake_edge_risks=["reference_only_source", "fair_value_reference_only_not_executable_leg"],
                    allowed_next_action="IGNORE_LOW_VALUE",
                    expected_value="LOW",
                )
            )
    sports_game_level_present = context.family_present("SPORTS_GAME_LEVEL") and (
        "sx_bet" in context.venues_for("SPORTS_GAME_LEVEL") or "sx_bet" in context.rv_profile_venues
    )
    if sports_game_level_present:
        for venue in ["kalshi", "polymarket"]:
            if venue not in context.venues_for("SPORTS_GAME_LEVEL"):
                rows.append(
                    _gap_row(
                        context,
                        family="SPORTS_GAME_LEVEL",
                        missing_platform_or_venue=venue,
                        opportunity_reason=(
                            "SX Bet game-level sports rows are present; collect matching game-level snapshots instead "
                            "of comparing against futures-scope markets."
                        ),
                        fake_edge_risks=[
                            "sports_game_level_vs_futures_scope_mismatch",
                            "settlement_basis_mismatch",
                            "missing_group_metadata",
                        ],
                        allowed_next_action="FETCH_SAVED_MARKET_SNAPSHOT",
                        expected_value=_family_expected_value(context, "SPORTS_GAME_LEVEL"),
                    )
                )
    elif context.family_present("SPORTS_CHAMPION") and "sx_bet" not in context.venues_for("SPORTS_CHAMPION"):
        rows.append(
            _gap_row(
                context,
                family="SPORTS_GAME_LEVEL",
                missing_platform_or_venue="sx_bet",
                opportunity_reason=(
                    "Sports outcome diagnostics are present; SX Bet saved game-level snapshots would clarify whether "
                    "game-level coverage is useful without loosening scope."
                ),
                fake_edge_risks=["sports_game_level_vs_futures_scope_mismatch", "missing_group_metadata"],
                allowed_next_action="FETCH_SAVED_MARKET_SNAPSHOT",
                expected_value=_family_expected_value(context, "SPORTS_CHAMPION"),
            )
        )
    for venue in sorted(context.rv_auth_required_venues & {"ibkr_forecastex", "prophetx"}):
        family = _best_auth_family(context)
        rows.append(
            _gap_row(
                context,
                family=family,
                missing_platform_or_venue=venue,
                opportunity_reason=(
                    "Saved platform profile indicates auth review is required; keep this at manual platform review "
                    "before adapter scoping."
                ),
                fake_edge_risks=["requires_auth_review", "region_eligibility_unknown", "platform_profile_only"],
                allowed_next_action="MANUAL_PLATFORM_REVIEW",
                expected_value="MEDIUM",
            )
        )
    return _dedupe_and_rank_rows(rows)


def _gap_row(
    context: _RadarContext,
    *,
    family: str,
    missing_platform_or_venue: str,
    opportunity_reason: str,
    fake_edge_risks: list[str],
    allowed_next_action: str,
    expected_value: str,
) -> dict[str, Any]:
    family = _normalise_family(family)
    existing_venues = context.venues_for(family)
    if family == "SPORTS_GAME_LEVEL" and not existing_venues:
        existing_venues = sorted(context.family_to_venues.get("SPORTS_CHAMPION", set()) | ({"sx_bet"} & context.rv_profile_venues))
    row = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "family": family,
        "missing_platform_or_venue": _normalise_venue(missing_platform_or_venue),
        "existing_venues": existing_venues,
        "signal_sources": context.sources_for(family),
        "opportunity_reason": opportunity_reason,
        "expected_value_of_fetch": expected_value,
        "ontology_priority_score": context.ontology_priority_score(family),
        "ontology_priority_reasons": context.ontology_priority_reasons(family),
        "fake_edge_risks": sorted(set(fake_edge_risks)),
        "required_fields_to_fetch": list(REQUIRED_FIELDS_TO_FETCH),
        "allowed_next_action": allowed_next_action,
    }
    if family == "SPORTS_GAME_LEVEL" and not any(row["signal_sources"].values()):
        row["signal_sources"] = context.sources_for("SPORTS_CHAMPION")
    return row


def _dedupe_and_rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["family"], row["missing_platform_or_venue"], row["allowed_next_action"])
        current = by_key.get(key)
        if current is None or _expected_rank(row["expected_value_of_fetch"]) > _expected_rank(current["expected_value_of_fetch"]):
            by_key[key] = row
    return sorted(
        by_key.values(),
        key=lambda row: (
            -_expected_rank(row["expected_value_of_fetch"]),
            row["family"],
            row["missing_platform_or_venue"],
            row["allowed_next_action"],
        ),
    )


def _recommended_platform_fetches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_platform: dict[str, dict[str, Any]] = {}
    for row in rows:
        platform = row["missing_platform_or_venue"]
        current = best_by_platform.get(platform)
        if current is None or _recommendation_rank(row) > _recommendation_rank(current):
            best_by_platform[platform] = {
                "missing_platform_or_venue": platform,
                "family": row["family"],
                "expected_value_of_fetch": row["expected_value_of_fetch"],
                "allowed_next_action": row["allowed_next_action"],
                "ontology_priority_score": row["ontology_priority_score"],
                "ontology_priority_reasons": list(row["ontology_priority_reasons"]),
            }
    return sorted(
        best_by_platform.values(),
        key=lambda row: (
            -_expected_rank(row["expected_value_of_fetch"]),
            -_int(row.get("ontology_priority_score")),
            row["missing_platform_or_venue"],
        ),
    )


def _recommended_family_fetches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_family: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = row["family"]
        current = best_by_family.get(family)
        if current is None or _recommendation_rank(row) > _recommendation_rank(current):
            best_by_family[family] = {
                "family": family,
                "missing_platform_or_venue": row["missing_platform_or_venue"],
                "expected_value_of_fetch": row["expected_value_of_fetch"],
                "allowed_next_action": row["allowed_next_action"],
                "ontology_priority_score": row["ontology_priority_score"],
                "ontology_priority_reasons": list(row["ontology_priority_reasons"]),
            }
    return sorted(
        best_by_family.values(),
        key=lambda row: (
            -_expected_rank(row["expected_value_of_fetch"]),
            -_int(row.get("ontology_priority_score")),
            row["family"],
        ),
    )


def _recommendation_rank(row: dict[str, Any]) -> tuple[int, int]:
    return (
        _expected_rank(str(row.get("expected_value_of_fetch") or "")),
        _int(row.get("ontology_priority_score")),
    )


def _family_expected_value(context: _RadarContext, family: str) -> str:
    family = _normalise_family(family)
    if context.family_persistence[family] > 0 or context.family_high_confidence[family] > 0 or context.family_scores[family] >= 75:
        return "HIGH"
    if family in {"BTC_THRESHOLD", "SPORTS_GAME_LEVEL", "SPORTS_CHAMPION"}:
        return "HIGH" if context.family_scores[family] >= 50 else "MEDIUM"
    return "MEDIUM" if context.family_scores[family] >= 25 else "LOW"


def _best_auth_family(context: _RadarContext) -> str:
    for family in ["FED_MEETING_RANGE", "BTC_THRESHOLD", "SPORTS_CHAMPION", "SPORTS_GAME_LEVEL"]:
        if context.family_present(family):
            return family
    return "PLATFORM_PROFILE"


def _content_blockers(context: _RadarContext) -> list[str]:
    blockers: set[str] = set()
    if not context.family_sources:
        blockers.add("no_graph_families_available")
    if not context.venues_seen and not context.rv_profile_venues:
        blockers.add("no_platform_or_venue_context_available")
    if "crypto_com_predict_cdna" not in context.rv_profile_venues:
        blockers.add("crypto_com_predict_cdna_profile_not_available")
    if not (context.rv_auth_required_venues & {"ibkr_forecastex", "prophetx"}):
        blockers.add("auth_review_platform_profiles_not_available")
    return sorted(blockers)


def _family_inference_audit_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_report, row_id_key, row in _audit_input_rows(report):
        details = _family_inference_details(row)
        rows.append(
            {
                "row_id": _first_text(row, [row_id_key]) or "unknown",
                "source_report": source_report,
                "inferred_family": details["inferred_family"],
                "matched_tokens": details["matched_tokens"],
                "text_snippet": details["text_snippet"],
                "reasons": details["reasons"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            }
        )
    return rows


def _audit_input_rows(report: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    specs = [
        ("trade_indicators", "signals", "signal_id", ["trade_indicator_report", "trade_indicators_report"]),
        ("probability_constraints", "probability_constraints", "constraint_id", ["probability_constraints_report"]),
        ("rv_investigation_packets", "investigation_packets", "packet_id", ["rv_investigation_packets_report"]),
    ]
    output: list[tuple[str, str, dict[str, Any]]] = []
    for source_report, row_key, row_id_key, container_keys in specs:
        output.extend((source_report, row_id_key, row) for row in _list_from_report(report, row_key))
        for container_key in container_keys:
            nested = report.get(container_key)
            if isinstance(nested, dict):
                output.extend((source_report, row_id_key, row) for row in _list_from_report(nested, row_key))
    return output


def _infer_family(row: dict[str, Any]) -> str:
    return str(_family_inference_details(row)["inferred_family"])


def _family_inference_details(row: dict[str, Any]) -> dict[str, Any]:
    explicit = _first_text(row, ["family", "formula_family", "market_family", "event_family"])
    if explicit:
        family = _normalise_family(explicit)
        return _family_inference_result(row, family, [f"explicit_family:{family}"], ["explicit_family_field"])
    text = _row_text(row)
    if "sports_game_level" in text or ("sx_bet" in text and (_word(text, "game") or _word(text, "match"))):
        return _family_inference_result(row, "SPORTS_GAME_LEVEL", ["sports_game_level"], ["sports_game_level_token"])
    if _word(text, "btc") or _word(text, "bitcoin") or _word(text, "crypto"):
        token = "btc" if _word(text, "btc") else "bitcoin" if _word(text, "bitcoin") else "crypto"
        return _family_inference_result(row, "BTC_THRESHOLD", [token], ["explicit_crypto_token"])
    if _word(text, "fed") or _word(text, "fomc") or "target_rate" in text or "target rate" in text:
        tokens = []
        if _word(text, "fed"):
            tokens.append("fed")
        if _word(text, "fomc"):
            tokens.append("fomc")
        if "target_rate" in text or "target rate" in text:
            tokens.append("target_rate")
        return _family_inference_result(row, "FED_MEETING_RANGE", tokens, ["explicit_fed_token"])
    if _word(text, "weather") or _word(text, "temperature"):
        token = "weather" if _word(text, "weather") else "temperature"
        return _family_inference_result(row, "WEATHER_RANGE", [token], ["weather_or_temperature_token"])
    if _word(text, "sports") or _word(text, "champion") or "world_series" in text or "world series" in text:
        return _family_inference_result(row, "SPORTS_CHAMPION", ["sports_or_champion"], ["sports_champion_token"])
    if _word(text, "election") or _word(text, "candidate") or _word(text, "referendum"):
        return _family_inference_result(row, "ELECTION_OUTCOME", ["election_or_referendum"], ["election_token"])
    # The fallback rules below intentionally do NOT classify bare "threshold"
    # or "range" hits as BTC_THRESHOLD / FED_MEETING_RANGE. After the
    # market_formulas field was added to probability constraint and signal
    # rows, every threshold-shaped market injects the literal word "threshold"
    # (and a generic family label like "GENERIC_THRESHOLD") into row text. A
    # blind fallback would misclassify OpenAI valuation, AGI, or any other
    # generic threshold market as BTC_THRESHOLD and silently erase legitimate
    # platform-expansion gap rows. We only promote to BTC_THRESHOLD when there
    # is an explicit BTC/Bitcoin/Crypto signal in the row text (handled above).
    if "threshold_ladder" in text and (_word(text, "btc") or _word(text, "bitcoin")):
        return _family_inference_result(row, "BTC_THRESHOLD", ["threshold_ladder", "btc"], ["btc_threshold_ladder"])
    if "range_bucket" in text and (_word(text, "fed") or _word(text, "fomc")):
        return _family_inference_result(row, "FED_MEETING_RANGE", ["range_bucket", "fed_or_fomc"], ["fed_range_bucket"])
    reasons = ["no_supported_family_tokens"]
    if "threshold" in text:
        reasons.append("generic_threshold_without_btc_token")
    if "range" in text:
        reasons.append("generic_range_without_fed_token")
    return _family_inference_result(row, "UNKNOWN", [], reasons)


def _family_inference_result(
    row: dict[str, Any],
    family: str,
    matched_tokens: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    text = " ".join(_row_text(row).split())
    return {
        "inferred_family": family,
        "matched_tokens": sorted(set(matched_tokens)),
        "reasons": sorted(set(reasons)),
        "text_snippet": text[:240],
    }


_WORD_TOKEN_CACHE: dict[str, re.Pattern[str]] = {}


def _word(text: str, token: str) -> bool:
    """Letter/digit-bounded match for a token.

    Standard ``\\b`` treats underscore as a word character, which would block
    matches like ``btc_over_120k``.  We need a boundary that fires on
    underscores, colons, dots, and spaces alike, so we use explicit
    ``[a-z0-9]`` negative lookarounds.  This still rejects substring noise such
    as ``robotic`` or ``abctc``.
    """

    pattern = _WORD_TOKEN_CACHE.get(token)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])")
        _WORD_TOKEN_CACHE[token] = pattern
    return bool(pattern.search(text))


def _normalise_family(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "SPORTS": "SPORTS_CHAMPION",
        "SPORTS_WINNER": "SPORTS_CHAMPION",
        "SPORTS_FUTURES": "SPORTS_CHAMPION",
        "FED_RANGE": "FED_MEETING_RANGE",
        "FOMC": "FED_MEETING_RANGE",
        "CRYPTO": "BTC_THRESHOLD",
        "BTC": "BTC_THRESHOLD",
    }
    return aliases.get(normalized, normalized)


def _normalise_venue(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    lower = value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")
    if not lower:
        return ""
    if "crypto" in lower and ("predict" in lower or "cdna" in lower):
        return "crypto_com_predict_cdna"
    if "forecastex" in lower or "ibkr" in lower:
        return "ibkr_forecastex"
    if "prophetx" in lower:
        return "prophetx"
    if "sx_bet" in lower or "sxbet" in lower:
        return "sx_bet"
    if "polymarket" in lower:
        return "polymarket"
    if "kalshi" in lower:
        return "kalshi"
    if lower in REFERENCE_ONLY_VENUES:
        return lower
    if "fixture" in lower:
        return "fixture"
    return lower


def _row_venues(row: dict[str, Any]) -> list[str]:
    venues = _string_list(row.get("venues_involved"))
    if not venues:
        venues = _string_list(row.get("venues"))
    if not venues:
        single = _first_text(row, ["venue", "platform", "exchange"])
        venues = [single] if single else []
    if not venues:
        venues = [_market_id_venue(market_id) for market_id in _row_market_ids(row)]
    return [venue for venue in (_normalise_venue(item) for item in venues) if venue]


def _row_market_ids(row: dict[str, Any]) -> list[str]:
    for key in ["markets_involved", "market_ids", "source_market_ids"]:
        values = _string_list(row.get(key))
        if values:
            return values
    return []


def _market_id_venue(market_id: str) -> str:
    if ":" in market_id:
        return _normalise_venue(market_id.split(":", 1)[0])
    return ""


def _iter_candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in [
        "platform_profiles",
        "platforms",
        "profiles",
        "rows",
        "markets",
        "signals",
        "hints",
        "investigation_packets",
        "platform_gap_rows",
    ]:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    if not rows and any(key in payload for key in ["platform", "venue", "exchange"]):
        rows.append(payload)
    return rows


def _row_requires_auth_review(row: dict[str, Any]) -> bool:
    text = _row_text(row)
    return (
        row.get("auth_required") is True
        or row.get("requires_auth_review") is True
        or "auth_required" in text
        or "requires_auth_review" in text
    )


def _row_is_profile_only(row: dict[str, Any]) -> bool:
    text = _row_text(row)
    return (
        row.get("profile_only") is True
        or row.get("fixture_only") is True
        or row.get("transport_ready") is False
        or "profile_only" in text
        or "fixture_only" in text
        or "platform_profile" in text
    )


def _row_is_reference_only(row: dict[str, Any], venue: str) -> bool:
    blockers = set(_string_list(row.get("review_blockers")) + _string_list(row.get("packet_blockers")))
    return (
        venue in REFERENCE_ONLY_VENUES
        or row.get("reference_only_source") is True
        or "reference_only_source" in blockers
    )


def _load_optional_report(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SchemaValidationError(f"{path} must contain a JSON object")
    return payload


def _load_relative_value_reports_dir(path: Path | str | None) -> tuple[list[dict[str, Any]], list[str]]:
    if path is None:
        # No --relative-value-reports-dir was supplied. The radar runs fine
        # without it (it is an optional enrichment input), so we do not emit a
        # blocker here — that would flood the daily summary with a flag that
        # represents normal usage, not a missing prerequisite.
        return [], []
    directory = Path(path)
    if not directory.exists():
        return [], ["missing_relative_value_reports_dir"]
    if not directory.is_dir():
        return [], ["relative_value_reports_path_not_directory"]
    reports: list[dict[str, Any]] = []
    blockers: list[str] = []
    for file_path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            blockers.append(f"unreadable_relative_value_report:{file_path.name}")
            continue
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["_source_file"] = file_path.name
            reports.append(payload)
    if not reports:
        blockers.append("no_relative_value_reports_loaded")
    return reports, blockers


def _list_from_report(report: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    value = report.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _row_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                parts.append(str(key))
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, (str, int, float)) and not isinstance(item, bool):
            parts.append(str(item))

    visit(value)
    return " ".join(parts).lower()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float)) and not isinstance(item, bool)]


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _expected_rank(value: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(value, 0)


def _validate_gap_row(row: dict[str, Any], path: str) -> None:
    required = [
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "family",
        "missing_platform_or_venue",
        "existing_venues",
        "signal_sources",
        "opportunity_reason",
        "expected_value_of_fetch",
        "ontology_priority_score",
        "ontology_priority_reasons",
        "fake_edge_risks",
        "required_fields_to_fetch",
        "allowed_next_action",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if not isinstance(row["family"], str) or not row["family"]:
        raise SchemaValidationError(f"{path}.family must be a non-empty string")
    if not isinstance(row["missing_platform_or_venue"], str) or not row["missing_platform_or_venue"]:
        raise SchemaValidationError(f"{path}.missing_platform_or_venue must be a non-empty string")
    if not isinstance(row["existing_venues"], list) or not all(isinstance(item, str) for item in row["existing_venues"]):
        raise SchemaValidationError(f"{path}.existing_venues must be a list of strings")
    sources = row["signal_sources"]
    if not isinstance(sources, dict) or set(sources) != SIGNAL_SOURCE_KEYS:
        raise SchemaValidationError(f"{path}.signal_sources must contain the required source keys")
    if not all(isinstance(value, bool) for value in sources.values()):
        raise SchemaValidationError(f"{path}.signal_sources values must be booleans")
    if row["expected_value_of_fetch"] not in EXPECTED_VALUES:
        raise SchemaValidationError(f"{path}.expected_value_of_fetch is unsupported")
    if not isinstance(row["ontology_priority_score"], int) or isinstance(row["ontology_priority_score"], bool):
        raise SchemaValidationError(f"{path}.ontology_priority_score must be an integer")
    if row["ontology_priority_score"] < 0:
        raise SchemaValidationError(f"{path}.ontology_priority_score must be non-negative")
    if not isinstance(row["ontology_priority_reasons"], list) or not all(
        isinstance(item, str) for item in row["ontology_priority_reasons"]
    ):
        raise SchemaValidationError(f"{path}.ontology_priority_reasons must be a list of strings")
    if row["allowed_next_action"] not in ALLOWED_NEXT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_next_action is unsupported")
    if not isinstance(row["fake_edge_risks"], list) or not all(isinstance(item, str) for item in row["fake_edge_risks"]):
        raise SchemaValidationError(f"{path}.fake_edge_risks must be a list of strings")
    fields = row["required_fields_to_fetch"]
    if not isinstance(fields, list) or set(fields) != REQUIRED_FETCH_FIELD_SET:
        raise SchemaValidationError(f"{path}.required_fields_to_fetch must contain all required fields")
    if not isinstance(row["opportunity_reason"], str) or not row["opportunity_reason"]:
        raise SchemaValidationError(f"{path}.opportunity_reason must be a non-empty string")
    _reject_prohibited_tokens(row)


def _validate_recommendation(row: dict[str, Any], path: str, required_identity_key: str) -> None:
    for key in [
        required_identity_key,
        "expected_value_of_fetch",
        "allowed_next_action",
        "ontology_priority_score",
        "ontology_priority_reasons",
    ]:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["expected_value_of_fetch"] not in EXPECTED_VALUES:
        raise SchemaValidationError(f"{path}.expected_value_of_fetch is unsupported")
    if row["allowed_next_action"] not in ALLOWED_NEXT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_next_action is unsupported")
    if not isinstance(row["ontology_priority_score"], int) or isinstance(row["ontology_priority_score"], bool):
        raise SchemaValidationError(f"{path}.ontology_priority_score must be an integer")
    if row["ontology_priority_score"] < 0:
        raise SchemaValidationError(f"{path}.ontology_priority_score must be non-negative")
    if not isinstance(row["ontology_priority_reasons"], list) or not all(
        isinstance(item, str) for item in row["ontology_priority_reasons"]
    ):
        raise SchemaValidationError(f"{path}.ontology_priority_reasons must be a list of strings")
    _reject_prohibited_tokens(row)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "ALLOWED_NEXT_ACTIONS",
    "EXPECTED_VALUES",
    "REFERENCE_ONLY_VENUES",
    "REQUIRED_FIELDS_TO_FETCH",
    "build_platform_expansion_radar_report",
    "render_platform_expansion_radar_markdown",
    "validate_platform_expansion_radar_report",
    "write_family_inference_audit_report",
    "write_platform_expansion_radar_report",
]
