from __future__ import annotations

from collections.abc import Iterable, Sequence

from relative_value.config import ScannerConfig
from relative_value.models import ACTION_SEVERITY, Action, NormalizedMarket, RelativeValueCandidate
from relative_value.models import SourceKind
from relative_value.scoring import score_pair


class RelativeValueScanner:
    def __init__(self, config: ScannerConfig | None = None) -> None:
        self.config = config or ScannerConfig()

    def scan(self, markets: Sequence[NormalizedMarket], include_ignore: bool = False) -> list[RelativeValueCandidate]:
        candidates: list[RelativeValueCandidate] = []
        for index, left in enumerate(markets):
            for right in markets[index + 1 :]:
                if left.venue == right.venue:
                    continue
                candidate = score_pair(left, right, self.config)
                if include_ignore or candidate.action != Action.IGNORE:
                    candidates.append(candidate)
        if not include_ignore:
            candidates = self._suppress_redundant_opposite_reference_candidates(candidates)
        return sorted(
            candidates,
            key=lambda item: (
                ACTION_SEVERITY[item.action],
                item.fee_adjusted_gap if item.fee_adjusted_gap is not None else -1.0,
                item.reference_gap if item.reference_gap is not None else -1.0,
                item.match.match_confidence,
            ),
            reverse=True,
        )

    def _suppress_redundant_opposite_reference_candidates(
        self,
        candidates: Sequence[RelativeValueCandidate],
    ) -> list[RelativeValueCandidate]:
        same_side_reference_keys = {
            key
            for candidate in candidates
            if "opposite_reference_outcome_inverted" not in candidate.reasons
            for key in [self._reference_candidate_key(candidate)]
            if key is not None
        }
        if not same_side_reference_keys:
            return list(candidates)
        return [
            candidate
            for candidate in candidates
            if "opposite_reference_outcome_inverted" not in candidate.reasons
            or self._reference_candidate_key(candidate) not in same_side_reference_keys
        ]

    def _reference_candidate_key(self, candidate: RelativeValueCandidate) -> tuple[str, str, str, str] | None:
        left = candidate.left
        right = candidate.right
        if left.source_kind == SourceKind.EXCHANGE and right.source_kind == SourceKind.SPORTSBOOK_REFERENCE:
            return (left.venue, left.market_id, right.venue, _sportsbook_event_id(right))
        if right.source_kind == SourceKind.EXCHANGE and left.source_kind == SourceKind.SPORTSBOOK_REFERENCE:
            return (right.venue, right.market_id, left.venue, _sportsbook_event_id(left))
        return None

    def scan_from_adapters(self, adapters: Iterable[object], include_ignore: bool = False) -> list[RelativeValueCandidate]:
        markets: list[NormalizedMarket] = []
        for adapter in adapters:
            markets.extend(adapter.load_markets())
        return self.scan(markets, include_ignore=include_ignore)


def _sportsbook_event_id(market: NormalizedMarket) -> str:
    raw_event_id = market.raw.get("event_id")
    if raw_event_id is not None:
        return str(raw_event_id)
    parts = market.market_id.split(":")
    if len(parts) >= 3:
        return parts[1]
    return market.market_id
