from __future__ import annotations

from graph_engine.semantics.normalizer import normalize_text


def wording_overlap(a: str, b: str) -> float:
    a_words = set(normalize_text(a).split())
    b_words = set(normalize_text(b).split())
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)

