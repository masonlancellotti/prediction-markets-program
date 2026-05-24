from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, settings
from data.storage import Storage
from research.market_making_replay import MarketMakingReplayBacktester, MarketMakingReplayConfig

_ADVERSE_THRESHOLD = 0.35


@dataclass(frozen=True)
class StrictMarketMakingCandidateDiagnosticsConfig:
    last_days: int = 7
    search_max_markets: int = 100
    min_replay_fills: int = 1
    min_recent_trades: int = 0
    min_spread_cents: float = float(settings.passive_min_spread_cents)
    min_depth: float = float(settings.passive_min_displayed_depth)
    stale_current_seconds: int = 180
    weather_only: bool = False


@dataclass(frozen=True)
class StrictMarketMakingCandidateDiagnosticsResult:
    summary: dict[str, Any]
    near_strict_markets: list[dict[str, Any]]

    def to_text(self) -> str:
        lines = [
            f"strict_market_making_candidate_diagnostics_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"strict_targets={self.summary.get('strict_target_count')} exploratory_targets={self.summary.get('exploratory_target_count')} "
            f"near_strict={self.summary.get('near_strict_count')}",
            f"weather_only={str(self.summary.get('weather_only')).lower()} research_only={str(self.summary.get('research_only')).lower()}",
            f"blockers={self.summary.get('blocker_counts')}",
            f"exports={self.summary.get('exports')}",
        ]
        lines.append("Closest near-strict markets:")
        for row in self.near_strict_markets[:10]:
            lines.append(
                f"- {row.get('ticker')} side={row.get('side')} category={row.get('category')} "
                f"fills={row.get('fills')} edge_net={_fmt(row.get('edge_net'))} adverse={_fmt(row.get('adverse_rate'))} "
                f"current_spread={_fmt(row.get('current_spread'))} fresh={str(row.get('fresh_current_book')).lower()} "
                f"missing={','.join(row.get('missing_requirements') or [])}"
            )
        lines.append("Recommendations:")
        for item in self.summary.get("recommendations") or []:
            lines.append(f"- {item}")
        return "\n".join(lines)


class StrictMarketMakingCandidateDiagnosticsReporter:
    """Read-only explanation of why strict paper basket targets are scarce."""

    def __init__(self, storage: Storage | None = None):
        self.storage = storage or Storage()

    def build(
        self,
        config: StrictMarketMakingCandidateDiagnosticsConfig | None = None,
        *,
        persist_exports: bool = True,
    ) -> StrictMarketMakingCandidateDiagnosticsResult:
        config = config or StrictMarketMakingCandidateDiagnosticsConfig()
        replay_config = MarketMakingReplayConfig(
            min_spread_cents=config.min_spread_cents,
            min_depth=config.min_depth,
            stale_current_seconds=config.stale_current_seconds,
            require_current_setup=False,
            weather_only=config.weather_only,
        )
        replay = MarketMakingReplayBacktester(storage=self.storage, config=replay_config).replay(
            last_days=config.last_days,
            max_markets=config.search_max_markets,
            persist_exports=False,
        )
        result = _diagnose_strict_candidates(replay.markets, replay.summary, config)
        if persist_exports:
            result.summary["exports"] = _export(result.summary, result.near_strict_markets)
        else:
            result.summary["exports"] = None
        return result


def _diagnose_strict_candidates(
    markets: list[dict[str, Any]],
    replay_summary: dict[str, Any],
    config: StrictMarketMakingCandidateDiagnosticsConfig,
) -> StrictMarketMakingCandidateDiagnosticsResult:
    strict: list[dict[str, Any]] = []
    exploratory: list[dict[str, Any]] = []
    near_strict: list[dict[str, Any]] = []
    blocker_counts = {
        "too_few_trade_print_fills": 0,
        "no_current_spread": 0,
        "spread_below_minimum": 0,
        "stale_or_expired": 0,
        "adverse_selection_high": 0,
        "missing_depth": 0,
        "insufficient_replay_support": 0,
    }
    for row in markets:
        blockers = _strict_blockers(row, config)
        for blocker in blockers:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        if not blockers:
            strict.append(row)
        elif _is_exploratory(row, config):
            exploratory.append(row)
        if "stale_or_expired" not in blockers:
            near_strict.append(_near_strict_row(row, blockers, config))

    near_strict = [row for row in near_strict if row["missing_requirements"]]
    near_strict = sorted(
        near_strict,
        key=lambda row: (
            row["blocker_distance"],
            row["fill_deficit"],
            row["net_edge_deficit"],
            row["adverse_excess"],
            -float(row.get("score") or 0.0),
        ),
    )
    summary = {
        "status": "STRICT_MARKET_MAKING_CANDIDATE_DIAGNOSTICS_OK",
        "message": _message(strict, near_strict),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "readiness_promotion": "none",
        "paper_or_live_readiness": "not_promoted",
        "weather_only": bool(config.weather_only),
        "last_days": int(config.last_days),
        "search_max_markets": int(config.search_max_markets),
        "min_replay_fills": int(config.min_replay_fills),
        "min_recent_trades": int(config.min_recent_trades),
        "min_spread_cents": float(config.min_spread_cents),
        "min_depth": float(config.min_depth),
        "adverse_threshold": _ADVERSE_THRESHOLD,
        "replay_summary": replay_summary,
        "markets_evaluated": int(len(markets)),
        "strict_target_count": int(len(strict)),
        "exploratory_target_count": int(len(exploratory)),
        "near_strict_count": int(len(near_strict)),
        "blocker_counts": blocker_counts,
        "recommendations": _recommend(strict, near_strict, blocker_counts),
        "disclaimer": (
            "Strict market-making candidate diagnostics are research-only. "
            "They do not lower thresholds, promote readiness, place orders, or treat exploratory targets as edge evidence."
        ),
    }
    return StrictMarketMakingCandidateDiagnosticsResult(summary=summary, near_strict_markets=near_strict[:50])


def _strict_blockers(row: dict[str, Any], config: StrictMarketMakingCandidateDiagnosticsConfig) -> list[str]:
    blockers: list[str] = []
    if bool(row.get("market_likely_expired")):
        blockers.append("stale_or_expired")
    if not bool(row.get("current_setup_ok")):
        reason = str(row.get("current_setup_reason") or "").lower()
        spread = _num(row.get("current_spread_cents"))
        depth = _num(row.get("current_displayed_depth"))
        if "stale" in reason or "expired" in reason or "market status" in reason:
            blockers.append("stale_or_expired")
        if spread is None:
            blockers.append("no_current_spread")
        elif spread < config.min_spread_cents or ("spread" in reason and "below minimum" in reason):
            blockers.append("spread_below_minimum")
        if depth is None or "depth" in reason:
            blockers.append("missing_depth")
        if "same-side bid" in reason or "opposing ask" in reason or "no latest orderbook" in reason:
            if "no_current_spread" not in blockers:
                blockers.append("no_current_spread")
    fills = int(row.get("fills") or 0)
    if fills < config.min_replay_fills:
        blockers.append("too_few_trade_print_fills")
    net_30 = float(row.get("avg_net_edge_30m_cents") or 0.0)
    if net_30 <= 0:
        blockers.append("insufficient_replay_support")
    adverse = float(row.get("adverse_fill_rate_30m") or 0.0)
    if adverse > _ADVERSE_THRESHOLD:
        blockers.append("adverse_selection_high")
    return list(dict.fromkeys(blockers))


def _is_exploratory(row: dict[str, Any], config: StrictMarketMakingCandidateDiagnosticsConfig) -> bool:
    return (
        bool(row.get("current_setup_ok"))
        and not bool(row.get("market_likely_expired"))
        and int(row.get("trades") or 0) >= config.min_recent_trades
    )


def _near_strict_row(row: dict[str, Any], blockers: list[str], config: StrictMarketMakingCandidateDiagnosticsConfig) -> dict[str, Any]:
    fills = int(row.get("fills") or 0)
    net_30 = float(row.get("avg_net_edge_30m_cents") or 0.0)
    adverse = float(row.get("adverse_fill_rate_30m") or 0.0)
    age = _num(row.get("current_book_age_seconds"))
    return {
        "ticker": row.get("market_ticker"),
        "side": row.get("best_side"),
        "category": _market_category(str(row.get("market_ticker") or "")),
        "fills": fills,
        "edge_net": net_30,
        "adverse_rate": adverse,
        "current_spread": _num(row.get("current_spread_cents")),
        "current_setup_ok": bool(row.get("current_setup_ok")),
        "current_setup_reason": row.get("current_setup_reason"),
        "fresh_current_book": bool(age is not None and age <= config.stale_current_seconds),
        "current_book_age_seconds": age,
        "missing_requirements": blockers,
        "blocker_distance": len(blockers),
        "fill_deficit": max(0, int(config.min_replay_fills) - fills),
        "net_edge_deficit": max(0.0, 0.01 - net_30),
        "adverse_excess": max(0.0, adverse - _ADVERSE_THRESHOLD),
        "score": float(row.get("score") or 0.0),
        "readiness": row.get("readiness"),
    }


def _recommend(strict: list[dict[str, Any]], near_strict: list[dict[str, Any]], blockers: dict[str, int]) -> list[str]:
    recommendations: list[str] = []
    if strict:
        recommendations.append("A longer paper basket is only reasonable if these strict targets remain fresh at run time.")
    else:
        recommendations.append("Collect more replay trade evidence before running strict-only paper baskets; this diagnostic found no strict targets.")
    if blockers.get("too_few_trade_print_fills", 0) > 0:
        recommendations.append("Prioritize longer read-only recording/replay windows for near-strict markets with too few trade-print fills.")
    if blockers.get("spread_below_minimum", 0) > 0:
        recommendations.append("Inspect spread-below-minimum cases before changing spread thresholds; do not lower thresholds automatically.")
    if blockers.get("adverse_selection_high", 0) > 0:
        recommendations.append("Do not promote high-adverse-selection markets; treat them as research red flags.")
    if not near_strict and not strict:
        recommendations.append("No close candidates were found; collect broader fresh orderbooks/trade prints before another basket run.")
    recommendations.append("Exploratory targets remain evidence collection only and should not be treated as proof of edge.")
    return recommendations


def _message(strict: list[dict[str, Any]], near_strict: list[dict[str, Any]]) -> str:
    if strict:
        return "Strict replay-supported market-making targets exist; review freshness and blockers before any paper basket."
    if near_strict:
        return "No strict targets found, but some fresh non-expired markets are close enough for research review."
    return "No strict or near-strict fresh targets found in the selected window."


def _market_category(ticker: str) -> str:
    upper = ticker.upper()
    if upper.startswith(("KXHIGH", "KXLOW", "KXRAIN", "KXSNOW", "KXTEMP", "KXHUMID", "KXWIND")):
        return "weather"
    if upper.startswith(("KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXATP", "KXMLS", "KXUEFA")):
        return "sports"
    if upper.startswith(("KXPRIMARY", "KXELECTION", "KXSENATE", "KXHOUSE", "KXPRES")):
        return "politics"
    if upper.startswith(("KXBTC", "KXETH", "KXCRYPTO")):
        return "crypto"
    return "other"


def _export(summary: dict[str, Any], near_strict: list[dict[str, Any]]) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    json_path = reports / "strict_market_making_candidate_diagnostics.json"
    md_path = reports / "strict_market_making_candidate_diagnostics.md"
    payload = {"summary": summary, "near_strict_markets": near_strict}
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(summary, near_strict), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _markdown(summary: dict[str, Any], near_strict: list[dict[str, Any]]) -> str:
    lines = [
        "# Strict Market-Making Candidate Diagnostics",
        "",
        f"Status: `{summary.get('status')}`",
        f"Research only: `{summary.get('research_only')}`",
        f"Strict targets: `{summary.get('strict_target_count')}`",
        f"Exploratory targets: `{summary.get('exploratory_target_count')}`",
        "",
        "## Blockers",
    ]
    for key, value in sorted((summary.get("blocker_counts") or {}).items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Closest Near-Strict Markets"])
    for row in near_strict[:20]:
        lines.append(
            f"- `{row.get('ticker')}` `{row.get('side')}` category=`{row.get('category')}` "
            f"fills={row.get('fills')} net30={_fmt(row.get('edge_net'))} "
            f"blockers={', '.join(row.get('missing_requirements') or [])}"
        )
    lines.extend(["", "## Recommendations"])
    for item in summary.get("recommendations") or []:
        lines.append(f"- {item}")
    lines.extend(["", summary.get("disclaimer", "")])
    return "\n".join(lines)


def _num(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _num(value)
    return "none" if number is None else f"{number:.3f}"
