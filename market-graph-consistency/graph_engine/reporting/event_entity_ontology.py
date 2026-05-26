from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph_engine.formula import MarketFormula, parse_fixture_market_formula
from graph_engine.models import GraphSnapshot, MarketNode
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


ENTITY_TYPES = {
    "SPORTS_TEAM",
    "SPORTS_GAME",
    "SPORTS_CHAMPIONSHIP",
    "CRYPTO_ASSET",
    "CRYPTO_THRESHOLD_EVENT",
    "FED_MEETING",
    "ECONOMIC_RELEASE",
    "ELECTION_CONTEST",
    "CANDIDATE_OR_PARTY",
    "WEATHER_STATION",
    "OTHER_UNKNOWN",
}
EVIDENCE_TYPES = {
    "structured_formula",
    "ticker_prefix",
    "event_slug",
    "explicit_metadata",
    "manual_fixture",
    "title_only_low_confidence",
}
CONFIDENCE_TIERS = {"HIGH", "MEDIUM", "LOW"}
EVIDENCE_RANK = {
    "structured_formula": 6,
    "explicit_metadata": 5,
    "ticker_prefix": 4,
    "event_slug": 3,
    "manual_fixture": 2,
    "title_only_low_confidence": 1,
}
CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
BANNER = (
    "Saved-file-only event/entity ontology. Rows are identity normalization hints and do not affect evaluator gates."
)
TITLE_ONLY_REASON = "title_only_hint_not_identity_proof"
LLM_ONLY_REASON = "llm_alias_advisory_not_identity_proof"
ELECTION_REASON = "contest_identity_not_payoff_equivalence"
RECOMMENDED_TASKS = {
    "ADD_EXPLICIT_EVENT_METADATA",
    "ADD_TICKER_ALIAS_REGISTRY",
    "REVIEW_TITLE_ONLY_LOW_CONFIDENCE",
    "ADD_WEATHER_STATION_METADATA",
    "ADD_SPORTS_GAME_ENTITY_KEYS",
    "REVIEW_CROSS_VENUE_ENTITY_CANDIDATES",
}


@dataclass
class _EntityAccumulator:
    entity_id: str
    entity_type: str
    canonical_name: str
    evidence_type: str
    confidence_tier: str
    aliases: set[str] = field(default_factory=set)
    source_market_ids: set[str] = field(default_factory=set)
    venues: set[str] = field(default_factory=set)
    blockers: set[str] = field(default_factory=set)
    not_identity_proof_reason: str | None = None

    def merge(
        self,
        *,
        canonical_name: str | None = None,
        aliases: list[str] | None = None,
        market_id: str | None = None,
        venue: str | None = None,
        evidence_type: str | None = None,
        confidence_tier: str | None = None,
        blockers: list[str] | None = None,
        not_identity_proof_reason: str | None = None,
    ) -> None:
        if canonical_name and EVIDENCE_RANK.get(evidence_type or self.evidence_type, 0) > EVIDENCE_RANK.get(self.evidence_type, 0):
            self.canonical_name = canonical_name
        for alias in aliases or []:
            normalized = _clean_alias(alias)
            if normalized:
                self.aliases.add(normalized)
        if market_id:
            self.source_market_ids.add(market_id)
        if venue:
            self.venues.add(venue)
        if evidence_type and EVIDENCE_RANK[evidence_type] > EVIDENCE_RANK[self.evidence_type]:
            self.evidence_type = evidence_type
        if confidence_tier and CONFIDENCE_RANK[confidence_tier] > CONFIDENCE_RANK[self.confidence_tier]:
            self.confidence_tier = confidence_tier
        for blocker in blockers or []:
            self.blockers.add(blocker)
        if not_identity_proof_reason and (
            self.not_identity_proof_reason is None or self.confidence_tier == "LOW"
        ):
            self.not_identity_proof_reason = not_identity_proof_reason

    def to_row(self) -> dict[str, Any]:
        reason = self.not_identity_proof_reason
        if self.confidence_tier == "LOW" and reason is None:
            reason = TITLE_ONLY_REASON
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "canonical_name": self.canonical_name,
            "aliases": sorted(self.aliases - {self.canonical_name}),
            "source_market_ids": sorted(self.source_market_ids),
            "venues": sorted(self.venues),
            "evidence_type": self.evidence_type,
            "confidence_tier": self.confidence_tier,
            "blockers": sorted(self.blockers),
            "not_identity_proof_reason": reason,
            "diagnostic_only": True,
        }


def build_event_entity_ontology_report(
    snapshot: GraphSnapshot,
    *,
    llm_hypotheses_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    formulas = {node.market_id: parse_fixture_market_formula(node) for node in snapshot.nodes.values()}
    entities: dict[str, _EntityAccumulator] = {}
    for node in snapshot.nodes.values():
        _add_node_entities(entities, node, formulas[node.market_id])
    _apply_llm_alias_hints(entities, snapshot, llm_hypotheses_report)
    rows = sorted((entity.to_row() for entity in entities.values()), key=lambda row: (row["entity_type"], row["entity_id"]))
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": BANNER,
        "entity_count": len(rows),
        "ontology_rows": rows,
        "summary": _summary(rows),
    }
    validate_event_entity_ontology_report(report)
    return report


def write_event_entity_ontology_report(
    snapshot: GraphSnapshot,
    json_output: Path | str,
    markdown_output: Path | str,
    *,
    llm_hypotheses_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_event_entity_ontology_report(snapshot, llm_hypotheses_report=llm_hypotheses_report)
    markdown = render_event_entity_ontology_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "event/entity ontology Markdown contains prohibited vocabulary: " + ", ".join(findings)
        )
    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def validate_event_entity_ontology_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("event/entity ontology must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("event/entity ontology must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("event/entity ontology actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("ontology_rows")
    if not isinstance(rows, list):
        raise SchemaValidationError("ontology_rows must be a list")
    if report.get("entity_count") != len(rows):
        raise SchemaValidationError("entity_count must match ontology_rows")
    for index, row in enumerate(rows):
        _validate_ontology_row(row, f"ontology_rows[{index}]")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("summary must be an object")
    for key in [
        "entities_by_type",
        "low_confidence_entities",
        "cross_venue_entity_candidates",
        "families_with_missing_entity_coverage",
        "recommended_next_entity_normalization_tasks",
    ]:
        if key not in summary:
            raise SchemaValidationError(f"summary.{key} is required")
    if not isinstance(summary["entities_by_type"], dict):
        raise SchemaValidationError("summary.entities_by_type must be an object")
    for entity_type, count in summary["entities_by_type"].items():
        if entity_type not in ENTITY_TYPES:
            raise SchemaValidationError(f"summary.entities_by_type contains unsupported type {entity_type!r}")
        if not isinstance(count, int) or isinstance(count, bool):
            raise SchemaValidationError("summary.entities_by_type values must be integers")
    for key in [
        "low_confidence_entities",
        "cross_venue_entity_candidates",
        "families_with_missing_entity_coverage",
        "recommended_next_entity_normalization_tasks",
    ]:
        if not isinstance(summary[key], list) or not all(isinstance(item, str) for item in summary[key]):
            raise SchemaValidationError(f"summary.{key} must be a list of strings")
    for task in summary["recommended_next_entity_normalization_tasks"]:
        if task not in RECOMMENDED_TASKS:
            raise SchemaValidationError(f"unsupported entity normalization task {task!r}")


def render_event_entity_ontology_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Market Graph Event Entity Ontology",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Entity count: `{report['entity_count']}`",
        "",
        "## Summary",
        "",
        f"- Entities by type: `{json.dumps(summary['entities_by_type'], sort_keys=True)}`",
        f"- Low confidence entities: `{len(summary['low_confidence_entities'])}`",
        f"- Cross-venue candidates: `{len(summary['cross_venue_entity_candidates'])}`",
        f"- Missing coverage families: `{', '.join(summary['families_with_missing_entity_coverage']) or 'none'}`",
        "",
        "## Recommended Entity Normalization Tasks",
        "",
    ]
    if summary["recommended_next_entity_normalization_tasks"]:
        lines.extend(f"- `{task}`" for task in summary["recommended_next_entity_normalization_tasks"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Ontology Rows",
            "",
            "| Entity | Type | Confidence | Evidence | Venues | Markets | Blockers |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if not report["ontology_rows"]:
        lines.append("| none |  |  |  |  |  |  |")
    for row in report["ontology_rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["canonical_name"]),
                    _md(row["entity_type"]),
                    _md(row["confidence_tier"]),
                    _md(row["evidence_type"]),
                    _md(", ".join(row["venues"])),
                    _md(", ".join(row["source_market_ids"])),
                    _md(", ".join(row["blockers"])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _add_node_entities(entities: dict[str, _EntityAccumulator], node: MarketNode, formula: MarketFormula) -> None:
    text = _node_text(node)
    asset = _crypto_asset_from_node(node, formula)
    if asset:
        _upsert(
            entities,
            entity_type="CRYPTO_ASSET",
            canonical_name=asset,
            aliases=_crypto_aliases(asset),
            node=node,
            evidence_type="structured_formula" if formula.family == "BTC_THRESHOLD" and formula.parse_quality >= 0.7 else "ticker_prefix",
            confidence_tier="HIGH" if formula.family == "BTC_THRESHOLD" and formula.parse_quality >= 0.7 else "MEDIUM",
        )
        threshold = formula.threshold if formula.family == "BTC_THRESHOLD" else _threshold_from_text(text)
        comparator = formula.comparator if formula.family == "BTC_THRESHOLD" else _comparator_from_text(text)
        if threshold is not None:
            date = formula.date or node.window or node.resolution_date
            threshold_name = f"{asset} threshold {comparator or '?'} {_format_number(threshold)} {formula.units or 'USD'} {date or 'unknown_window'}"
            _upsert(
                entities,
                entity_type="CRYPTO_THRESHOLD_EVENT",
                canonical_name=threshold_name,
                aliases=[threshold_name, f"{asset} {_format_number(threshold)} threshold"],
                node=node,
                evidence_type="structured_formula" if formula.family == "BTC_THRESHOLD" and formula.parse_quality >= 0.7 else "ticker_prefix",
                confidence_tier="HIGH" if formula.family == "BTC_THRESHOLD" and formula.parse_quality >= 0.7 else "MEDIUM",
                blockers=formula.blockers,
            )
    if formula.family == "FED_MEETING_RANGE" or _is_fed_node(node):
        meeting_date = formula.meeting_date or node.window or node.resolution_date
        evidence = "structured_formula" if formula.family == "FED_MEETING_RANGE" and formula.parse_quality >= 0.7 else "manual_fixture"
        blockers = list(formula.blockers)
        if not meeting_date:
            blockers.append("missing_meeting_date")
        if not node.settlement_source:
            blockers.append("missing_source")
        _upsert(
            entities,
            entity_type="FED_MEETING",
            canonical_name=f"FOMC meeting {meeting_date or 'unknown'}",
            aliases=["Federal Reserve meeting", f"Fed meeting {meeting_date or 'unknown'}"],
            node=node,
            evidence_type=evidence,
            confidence_tier="HIGH" if evidence == "structured_formula" and not blockers else "MEDIUM",
            blockers=blockers,
        )
    if _is_election_node(node):
        contest_name = _election_contest_name(node)
        _upsert(
            entities,
            entity_type="ELECTION_CONTEST",
            canonical_name=contest_name,
            aliases=[contest_name, "example election"],
            node=node,
            evidence_type="manual_fixture",
            confidence_tier="MEDIUM",
            not_identity_proof_reason=ELECTION_REASON,
        )
        for entity in node.entities:
            if entity and entity.lower() != "example election":
                _upsert(
                    entities,
                    entity_type="CANDIDATE_OR_PARTY",
                    canonical_name=entity,
                    aliases=[entity],
                    node=node,
                    evidence_type="manual_fixture",
                    confidence_tier="MEDIUM",
                    not_identity_proof_reason=ELECTION_REASON,
                )
    if _is_sports_node(node):
        sports_entity = _sports_entity_name(node)
        evidence = "explicit_metadata" if _has_specific_entity(node) else "title_only_low_confidence"
        confidence = "MEDIUM" if evidence == "explicit_metadata" else "LOW"
        blockers = [] if evidence == "explicit_metadata" else ["title_only_low_confidence"]
        _upsert(
            entities,
            entity_type="SPORTS_TEAM",
            canonical_name=sports_entity,
            aliases=_sports_aliases(node, sports_entity, structured=evidence == "explicit_metadata"),
            node=node,
            evidence_type=evidence,
            confidence_tier=confidence,
            blockers=blockers,
            not_identity_proof_reason=None if evidence == "explicit_metadata" else TITLE_ONLY_REASON,
        )
        championship = _sports_championship_name(node, sports_entity)
        if championship:
            _upsert(
                entities,
                entity_type="SPORTS_CHAMPIONSHIP",
                canonical_name=championship,
                aliases=[championship],
                node=node,
                evidence_type=evidence,
                confidence_tier=confidence,
                blockers=blockers,
                not_identity_proof_reason=None if evidence == "explicit_metadata" else TITLE_ONLY_REASON,
            )
    if _is_weather_node(node):
        station = _weather_station_name(node)
        evidence = "explicit_metadata" if node.raw.get("station_id") or node.raw.get("station") else "title_only_low_confidence"
        _upsert(
            entities,
            entity_type="WEATHER_STATION",
            canonical_name=station,
            aliases=[station],
            node=node,
            evidence_type=evidence,
            confidence_tier="MEDIUM" if evidence == "explicit_metadata" else "LOW",
            blockers=[] if evidence == "explicit_metadata" else ["missing_weather_station_metadata"],
            not_identity_proof_reason=None if evidence == "explicit_metadata" else TITLE_ONLY_REASON,
        )
    if _is_economic_release_node(node):
        release_name = node.observable or _clean_alias(node.entities[0]) if node.entities else "economic release"
        _upsert(
            entities,
            entity_type="ECONOMIC_RELEASE",
            canonical_name=release_name,
            aliases=[release_name],
            node=node,
            evidence_type="explicit_metadata" if node.observable else "title_only_low_confidence",
            confidence_tier="MEDIUM" if node.observable else "LOW",
            blockers=[] if node.observable else ["missing_release_metadata"],
            not_identity_proof_reason=None if node.observable else TITLE_ONLY_REASON,
        )


def _apply_llm_alias_hints(
    entities: dict[str, _EntityAccumulator],
    snapshot: GraphSnapshot,
    llm_hypotheses_report: dict[str, Any] | None,
) -> None:
    if not isinstance(llm_hypotheses_report, dict):
        return
    market_to_entities: dict[str, list[_EntityAccumulator]] = defaultdict(list)
    for entity in entities.values():
        for market_id in entity.source_market_ids:
            market_to_entities[market_id].append(entity)
    for row in llm_hypotheses_report.get("validated_hypotheses", []):
        if not isinstance(row, dict):
            continue
        aliases = _llm_aliases(row)
        market_ids = [item for item in row.get("source_market_ids", []) if isinstance(item, str)]
        touched = False
        for market_id in market_ids:
            for entity in market_to_entities.get(market_id, []):
                entity.merge(
                    aliases=aliases,
                    blockers=["llm_alias_advisory_only"],
                    not_identity_proof_reason=LLM_ONLY_REASON if entity.confidence_tier == "LOW" else entity.not_identity_proof_reason,
                )
                touched = True
        if not touched and market_ids:
            known_nodes = [snapshot.nodes[market_id] for market_id in market_ids if market_id in snapshot.nodes]
            canonical = aliases[0] if aliases else f"LLM suggested entity {row.get('hypothesis_id') or 'unknown'}"
            entity_id = _entity_id("OTHER_UNKNOWN", canonical)
            accumulator = entities.setdefault(
                entity_id,
                _EntityAccumulator(
                    entity_id=entity_id,
                    entity_type="OTHER_UNKNOWN",
                    canonical_name=canonical,
                    evidence_type="title_only_low_confidence",
                    confidence_tier="LOW",
                    blockers={"llm_alias_advisory_only"},
                    not_identity_proof_reason=LLM_ONLY_REASON,
                ),
            )
            for node in known_nodes:
                accumulator.merge(aliases=aliases, market_id=node.market_id, venue=node.venue)


def _upsert(
    entities: dict[str, _EntityAccumulator],
    *,
    entity_type: str,
    canonical_name: str,
    aliases: list[str],
    node: MarketNode,
    evidence_type: str,
    confidence_tier: str,
    blockers: list[str] | None = None,
    not_identity_proof_reason: str | None = None,
) -> None:
    canonical_name = _clean_alias(canonical_name)
    if not canonical_name:
        return
    entity_id = _entity_id(entity_type, canonical_name)
    accumulator = entities.setdefault(
        entity_id,
        _EntityAccumulator(
            entity_id=entity_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            evidence_type=evidence_type,
            confidence_tier=confidence_tier,
        ),
    )
    accumulator.merge(
        canonical_name=canonical_name,
        aliases=aliases + [canonical_name],
        market_id=node.market_id,
        venue=node.venue,
        evidence_type=evidence_type,
        confidence_tier=confidence_tier,
        blockers=blockers,
        not_identity_proof_reason=not_identity_proof_reason,
    )


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(row["entity_type"] for row in rows)
    low_confidence = [row["entity_id"] for row in rows if row["confidence_tier"] == "LOW"]
    cross_venue = [
        row["entity_id"]
        for row in rows
        if len({_venue_root(venue) for venue in row["venues"]}) > 1
    ]
    missing: set[str] = set()
    if not any(row["entity_type"] == "SPORTS_GAME" for row in rows):
        missing.add("SPORTS_GAME")
    if not any(row["entity_type"] == "WEATHER_STATION" for row in rows):
        missing.add("WEATHER_STATION")
    if not any(row["entity_type"] == "ECONOMIC_RELEASE" for row in rows):
        missing.add("ECONOMIC_RELEASE")
    if low_confidence:
        missing.add("TITLE_ONLY_LOW_CONFIDENCE")
    tasks: set[str] = set()
    if low_confidence:
        tasks.add("REVIEW_TITLE_ONLY_LOW_CONFIDENCE")
        tasks.add("ADD_EXPLICIT_EVENT_METADATA")
    if any(row["entity_type"] == "CRYPTO_ASSET" for row in rows):
        tasks.add("ADD_TICKER_ALIAS_REGISTRY")
    if "WEATHER_STATION" in missing:
        tasks.add("ADD_WEATHER_STATION_METADATA")
    if "SPORTS_GAME" in missing:
        tasks.add("ADD_SPORTS_GAME_ENTITY_KEYS")
    if cross_venue:
        tasks.add("REVIEW_CROSS_VENUE_ENTITY_CANDIDATES")
    return {
        "entities_by_type": dict(sorted(by_type.items())),
        "low_confidence_entities": sorted(low_confidence),
        "cross_venue_entity_candidates": sorted(cross_venue),
        "families_with_missing_entity_coverage": sorted(missing),
        "recommended_next_entity_normalization_tasks": sorted(tasks),
    }


def _validate_ontology_row(row: dict[str, Any], path: str) -> None:
    required = [
        "entity_id",
        "entity_type",
        "canonical_name",
        "aliases",
        "source_market_ids",
        "venues",
        "evidence_type",
        "confidence_tier",
        "blockers",
        "not_identity_proof_reason",
        "diagnostic_only",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["entity_type"] not in ENTITY_TYPES:
        raise SchemaValidationError(f"{path}.entity_type is unsupported")
    if row["evidence_type"] not in EVIDENCE_TYPES:
        raise SchemaValidationError(f"{path}.evidence_type is unsupported")
    if row["confidence_tier"] not in CONFIDENCE_TIERS:
        raise SchemaValidationError(f"{path}.confidence_tier is unsupported")
    if row["evidence_type"] == "title_only_low_confidence" and row["confidence_tier"] != "LOW":
        raise SchemaValidationError(f"{path}.title-only evidence must be LOW confidence")
    if row["confidence_tier"] == "LOW" and not row["not_identity_proof_reason"]:
        raise SchemaValidationError(f"{path}.LOW rows need not_identity_proof_reason")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    for key in ["aliases", "source_market_ids", "venues", "blockers"]:
        if not isinstance(row[key], list) or not all(isinstance(item, str) for item in row[key]):
            raise SchemaValidationError(f"{path}.{key} must be a list of strings")
    if not isinstance(row["entity_id"], str) or not row["entity_id"]:
        raise SchemaValidationError(f"{path}.entity_id must be a non-empty string")
    if not isinstance(row["canonical_name"], str) or not row["canonical_name"]:
        raise SchemaValidationError(f"{path}.canonical_name must be a non-empty string")
    _reject_prohibited_tokens(row)


def _crypto_asset_from_node(node: MarketNode, formula: MarketFormula) -> str | None:
    if formula.asset:
        return formula.asset.upper()
    text = _node_text(node)
    if re.search(r"\bbtc\b|bitcoin", text):
        return "BTC"
    if re.search(r"\beth\b|ethereum", text):
        return "ETH"
    return None


def _crypto_aliases(asset: str) -> list[str]:
    if asset == "BTC":
        return ["BTC", "Bitcoin"]
    if asset == "ETH":
        return ["ETH", "Ethereum"]
    return [asset]


def _threshold_from_text(text: str) -> float | None:
    match = re.search(r"(?:above|over|at least|below|under)\s+\$?([0-9]+(?:\.[0-9]+)?)\s*(k|m|b)?", text)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    elif suffix == "b":
        value *= 1_000_000_000
    return value


def _comparator_from_text(text: str) -> str | None:
    if re.search(r"\b(at least|above|over)\b", text):
        return ">="
    if re.search(r"\b(below|under)\b", text):
        return "<"
    return None


def _is_fed_node(node: MarketNode) -> bool:
    text = _node_text(node)
    return "fed" in text or "fomc" in text or "federal reserve" in text


def _is_election_node(node: MarketNode) -> bool:
    if "range-bucket" in node.themes or "range_bucket" in node.themes:
        return False
    text = _node_text(node)
    if "turnout" in text and "candidate" not in text:
        return False
    return "election" in text or "candidate" in text or "referendum" in text


def _is_sports_node(node: MarketNode) -> bool:
    text = _node_text(node)
    return "sports" in text or "champion" in text or "world series" in text or "league championship" in text


def _is_weather_node(node: MarketNode) -> bool:
    text = _node_text(node)
    return "weather" in text or "temperature" in text or "station" in text


def _is_economic_release_node(node: MarketNode) -> bool:
    text = _node_text(node)
    return "cpi" in text or "inflation" in text or "jobs report" in text or "economic release" in text


def _has_specific_entity(node: MarketNode) -> bool:
    return bool(node.entities and node.entities != ["Test Entity"])


def _election_contest_name(node: MarketNode) -> str:
    date = node.resolution_date or node.window or "unknown"
    if "example election" in _node_text(node):
        return f"Example election {date}"
    return f"Election contest {date}"


def _sports_entity_name(node: MarketNode) -> str:
    if _has_specific_entity(node):
        return node.entities[0]
    match = re.match(r"(.+?)\s+wins\b", node.title, flags=re.IGNORECASE)
    if match:
        return _clean_alias(match.group(1))
    return _clean_alias(node.entities[0]) if node.entities else "Unknown sports entity"


def _sports_aliases(node: MarketNode, sports_entity: str, *, structured: bool) -> list[str]:
    aliases = [sports_entity]
    if structured:
        match = re.match(r"(.+?)\s+wins\b", node.title, flags=re.IGNORECASE)
        if match:
            aliases.append(_clean_alias(match.group(1)))
    return aliases


def _sports_championship_name(node: MarketNode, sports_entity: str) -> str | None:
    text = _node_text(node)
    if "world series" in text:
        return f"{sports_entity} World Series {node.window or node.resolution_date or 'unknown'}"
    if "league championship" in text or "champion" in text:
        return f"{sports_entity} championship {node.window or node.resolution_date or 'unknown'}"
    return None


def _weather_station_name(node: MarketNode) -> str:
    value = node.raw.get("station_id") or node.raw.get("station") or node.raw.get("weather_station")
    if value:
        return str(value)
    if node.entities:
        return node.entities[0]
    return "Unknown weather station"


def _llm_aliases(row: dict[str, Any]) -> list[str]:
    aliases = [item for item in row.get("suggested_aliases", []) if isinstance(item, str)]
    claim = str(row.get("natural_language_claim") or "")
    if re.search(r"\bbtc\b|bitcoin", claim, flags=re.IGNORECASE):
        aliases.extend(["BTC", "Bitcoin"])
    if re.search(r"\beth\b|ethereum", claim, flags=re.IGNORECASE):
        aliases.extend(["ETH", "Ethereum"])
    if not aliases and isinstance(row.get("event_class"), str):
        aliases.append(str(row["event_class"]))
    return [_clean_alias(alias) for alias in aliases if _clean_alias(alias)]


def _node_text(node: MarketNode) -> str:
    raw_text = " ".join(f"{key} {value}" for key, value in node.raw.items() if isinstance(value, (str, int, float)))
    return " ".join(
        [
            node.market_id,
            node.venue,
            node.title,
            node.canonical_text,
            node.resolution_criteria,
            node.resolution_date,
            node.observable or "",
            node.window or "",
            node.settlement_source or "",
            " ".join(node.entities),
            " ".join(node.themes),
            raw_text,
        ]
    ).lower()


def _entity_id(entity_type: str, canonical_name: str) -> str:
    return f"entity:{entity_type.lower()}:{_slug(canonical_name)}"


def _slug(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_") or "unknown"


def _venue_root(venue: str) -> str:
    """Return a normalized base venue (e.g. ``fixture_payoff`` -> ``fixture``).

    Used by the ontology cross-venue summary so that auxiliary venue tags that
    are siblings of the same underlying source do not falsely look like
    independent platforms.
    """

    cleaned = str(venue or "").strip().lower()
    if not cleaned:
        return ""
    return cleaned.split("_", 1)[0]


def _clean_alias(value: str) -> str:
    cleaned = " ".join(str(value).strip().split())
    return cleaned


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "ENTITY_TYPES",
    "EVIDENCE_TYPES",
    "build_event_entity_ontology_report",
    "render_event_entity_ontology_markdown",
    "validate_event_entity_ontology_report",
    "write_event_entity_ontology_report",
]
