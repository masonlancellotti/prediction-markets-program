from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceType(str, Enum):
    EXECUTABLE_VENUE = "EXECUTABLE_VENUE"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    SIGNAL_ONLY = "SIGNAL_ONLY"
    DO_NOT_USE_YET = "DO_NOT_USE_YET"


class ImplementationStatus(str, Enum):
    IMPLEMENTED_READ_ONLY = "IMPLEMENTED_READ_ONLY"
    FIXTURE_ONLY = "FIXTURE_ONLY"
    PLANNED_NOT_IMPLEMENTED = "PLANNED_NOT_IMPLEMENTED"


EFFECT_CANDIDATE_PAIR = "candidate_pair"
EFFECT_WATCH_DIAGNOSTICS = "watch_diagnostics"
EFFECT_DISCOVERY_CLUSTERING = "discovery_semantic_clustering"


class UnknownSourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceEntry:
    source_id: str
    display_name: str
    source_type: SourceType
    implementation_status: ImplementationStatus
    allowed_effects: tuple[str, ...]
    notes: str

    @property
    def is_implemented(self) -> bool:
        return self.implementation_status != ImplementationStatus.PLANNED_NOT_IMPLEMENTED

    @property
    def is_executable_venue(self) -> bool:
        return self.source_type == SourceType.EXECUTABLE_VENUE

    @property
    def can_create_candidate_pair(self) -> bool:
        return self.is_executable_venue and self.is_implemented and EFFECT_CANDIDATE_PAIR in self.allowed_effects

    @property
    def can_inform_watch_or_diagnostics(self) -> bool:
        return EFFECT_WATCH_DIAGNOSTICS in self.allowed_effects

    @property
    def can_inform_discovery_or_clustering(self) -> bool:
        return EFFECT_DISCOVERY_CLUSTERING in self.allowed_effects

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "source_type": self.source_type.value,
            "implementation_status": self.implementation_status.value,
            "allowed_effects": list(self.allowed_effects),
            "can_create_candidate_pair": self.can_create_candidate_pair,
            "notes": self.notes,
        }


_EXECUTABLE_EFFECTS = (
    EFFECT_CANDIDATE_PAIR,
    EFFECT_WATCH_DIAGNOSTICS,
    EFFECT_DISCOVERY_CLUSTERING,
)
_REFERENCE_EFFECTS = (EFFECT_WATCH_DIAGNOSTICS,)
_SIGNAL_EFFECTS = (EFFECT_DISCOVERY_CLUSTERING,)


SOURCE_REGISTRY: dict[str, SourceEntry] = {
    "kalshi": SourceEntry(
        source_id="kalshi",
        display_name="Kalshi",
        source_type=SourceType.EXECUTABLE_VENUE,
        implementation_status=ImplementationStatus.IMPLEMENTED_READ_ONLY,
        allowed_effects=_EXECUTABLE_EFFECTS,
        notes="Read-only discovery and orderbook enrichment exist; no trading or account access.",
    ),
    "polymarket": SourceEntry(
        source_id="polymarket",
        display_name="Polymarket",
        source_type=SourceType.EXECUTABLE_VENUE,
        implementation_status=ImplementationStatus.IMPLEMENTED_READ_ONLY,
        allowed_effects=_EXECUTABLE_EFFECTS,
        notes="Read-only discovery and orderbook enrichment exist; no wallet, CLOB execution, or auth.",
    ),
    "forecastex_ibkr": SourceEntry(
        source_id="forecastex_ibkr",
        display_name="ForecastEx / IBKR",
        source_type=SourceType.EXECUTABLE_VENUE,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=(),
        notes="Potential executable venue later; design-only read-only boundary exists, but live transport is blocked by account/API permission and instrument work.",
    ),
    "sx_bet": SourceEntry(
        source_id="sx_bet",
        display_name="SX Bet",
        source_type=SourceType.EXECUTABLE_VENUE,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=(),
        notes="Potential executable venue later; only read-only public market/orderbook research is allowed before any wallet/signing work.",
    ),
    "azuro": SourceEntry(
        source_id="azuro",
        display_name="Azuro",
        source_type=SourceType.DO_NOT_USE_YET,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=(),
        notes="On-chain/protocol-style liquidity model does not fit the current schema-v1 executable venue path cleanly.",
    ),
    "omen_gnosis": SourceEntry(
        source_id="omen_gnosis",
        display_name="Omen / Gnosis Conditional Tokens",
        source_type=SourceType.DO_NOT_USE_YET,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=(),
        notes="Protocol/indexer-style conditional-token markets require separate schema and settlement-token analysis before use.",
    ),
    "predictit": SourceEntry(
        source_id="predictit",
        display_name="PredictIt",
        source_type=SourceType.DO_NOT_USE_YET,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=(),
        notes="May have public read-only market data, but not treated as executable unless permitted execution API support is proven.",
    ),
    "manifold": SourceEntry(
        source_id="manifold",
        display_name="Manifold",
        source_type=SourceType.SIGNAL_ONLY,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=_SIGNAL_EFFECTS,
        notes="Planned signal/discovery source only; not an executable candidate venue.",
    ),
    "metaculus": SourceEntry(
        source_id="metaculus",
        display_name="Metaculus",
        source_type=SourceType.SIGNAL_ONLY,
        implementation_status=ImplementationStatus.PLANNED_NOT_IMPLEMENTED,
        allowed_effects=_SIGNAL_EFFECTS,
        notes="Planned forecast signal source only; not an executable candidate venue.",
    ),
    "the_odds_api": SourceEntry(
        source_id="the_odds_api",
        display_name="The Odds API / Sportsbooks",
        source_type=SourceType.REFERENCE_ONLY,
        implementation_status=ImplementationStatus.IMPLEMENTED_READ_ONLY,
        allowed_effects=_REFERENCE_EFFECTS,
        notes="Read-only sportsbook reference snapshots exist; odds are reference prices only and cannot create tradable candidates.",
    ),
    "sportsbooks": SourceEntry(
        source_id="sportsbooks",
        display_name="Sportsbooks",
        source_type=SourceType.REFERENCE_ONLY,
        implementation_status=ImplementationStatus.FIXTURE_ONLY,
        allowed_effects=_REFERENCE_EFFECTS,
        notes="Generic sportsbook/reference bucket; never executable in this scanner.",
    ),
}


def get_source_entry(source_id: str) -> SourceEntry:
    normalized = _normalize_source_id(source_id)
    try:
        return SOURCE_REGISTRY[normalized]
    except KeyError as exc:
        raise UnknownSourceError(f"unknown source: {source_id}") from exc


def is_executable_candidate_source(source_id: str) -> bool:
    return get_source_entry(source_id).can_create_candidate_pair


def can_create_tradable_candidate_pair(left_source_id: str, right_source_id: str) -> bool:
    left = get_source_entry(left_source_id)
    right = get_source_entry(right_source_id)
    return left.can_create_candidate_pair and right.can_create_candidate_pair


def source_registry_report() -> list[dict[str, object]]:
    return [entry.to_dict() for entry in SOURCE_REGISTRY.values()]


def _normalize_source_id(source_id: str) -> str:
    return source_id.strip().lower().replace("-", "_").replace(" ", "_")
