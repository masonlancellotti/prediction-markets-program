from __future__ import annotations

from typing import Any


TRUSTED_EXHAUSTIVE_SOURCES = frozenset(
    {
        "kalshi_event_metadata",
        "polymarket_event_metadata",
        "local_manifest_v1",
    }
)


def exhaustive_evidence_trust_blockers(
    *,
    source: Any,
    is_exhaustive: bool,
    venue_native: bool = False,
    trusted_local_manifest: bool = False,
) -> list[str]:
    blockers: list[str] = []
    if not is_exhaustive:
        blockers.append("not_explicitly_exhaustive")
    if not isinstance(source, str) or not source.strip():
        blockers.append("missing_exhaustive_evidence_source")
        return blockers
    normalized = source.strip().lower()
    if normalized not in TRUSTED_EXHAUSTIVE_SOURCES:
        blockers.append("exhaustive_evidence_source_not_trusted")
        return blockers
    if normalized in {"kalshi_event_metadata", "polymarket_event_metadata"} and not venue_native:
        blockers.append("venue_native_exhaustive_evidence_required")
    if normalized == "local_manifest_v1" and not trusted_local_manifest:
        blockers.append("trusted_local_manifest_required")
    return blockers


def has_reference_only_flag(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("reference_only") is True:
        return True
    for key in ("source_kind", "venue_type", "source_type", "execution_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower() in {"reference", "reference_only", "non_executable_reference"}:
            return True
    raw = payload.get("raw")
    if isinstance(raw, dict):
        return has_reference_only_flag(raw)
    return False
