from __future__ import annotations


def unique_entities(*entity_lists: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for entities in entity_lists:
        for entity in entities:
            normalized = entity.strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                result.append(normalized)
    return result

