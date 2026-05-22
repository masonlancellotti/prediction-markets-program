from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from config import PROJECT_ROOT, settings
from data.storage import Storage
from live.paper_market_maker import PaperMakerSide, PaperMarketMaker, PaperMarketMakerConfig, PaperMarketMakerResult
from research.market_making_replay import MarketMakingReplayBacktester, MarketMakingReplayConfig


@dataclass(frozen=True)
class PaperMarketMakingBasketConfig:
    """Paper-only basket runner for faster market-making evidence collection."""

    last_days: int = 1
    search_max_markets: int = 100
    max_targets: int = 5
    min_replay_fills: int = 1
    min_recent_trades: int = 0
    include_exploratory: bool = True
    refresh_candidates_minutes: float = 15.0
    quantity: float = 1.0
    max_position: float = 5.0
    max_open_quotes: int = 1
    improve_cents: float = 1.0
    min_spread_cents: float = float(settings.passive_min_spread_cents)
    min_depth: float = float(settings.passive_min_displayed_depth)
    quote_ttl_seconds: int = 300
    quote_spacing_seconds: int = 300
    stale_orderbook_seconds: int = 180
    interval_seconds: int = 30
    duration_minutes: float | None = None
    dry_run: bool = False
    weather_only: bool = False


@dataclass(frozen=True)
class PaperMarketMakingBasketResult:
    summary: dict[str, Any]
    targets: list[dict[str, Any]]
    target_results: list[dict[str, Any]]
    actions: list[dict[str, Any]]

    def to_text(self) -> str:
        lines = [
            f"paper_market_making_basket_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"targets={self.summary.get('targets')} strict={self.summary.get('strict_targets')} exploratory={self.summary.get('exploratory_targets')} "
            f"quotes={self.summary.get('quotes_total')} open={self.summary.get('open_quotes')} filled={self.summary.get('filled_quotes')} "
            f"cancelled={self.summary.get('cancelled_quotes')}",
            f"fill_rate={self.summary.get('fill_rate'):.3f} avg_edge_30m={_fmt(self.summary.get('avg_future_edge_30m_cents'))} "
            f"future30_n={self.summary.get('future_edge_30m_observations')} adverse30={self.summary.get('adverse_fill_rate_30m'):.3f}",
            f"weather_only={str(self.summary.get('weather_only')).lower()}",
            f"selector_verdict={self.summary.get('selector_verdict')} selector_fills={self.summary.get('selector_fills')} "
            f"current_targets={self.summary.get('selector_current_targets')} replay_supported_current_targets={self.summary.get('selector_replay_supported_current_targets')}",
            f"target_hygiene=raw={self.summary.get('raw_candidate_targets')} "
            f"expired_removed={self.summary.get('expired_or_stale_targets_removed')} "
            f"survived={self.summary.get('survived_expiry_filter')} "
            f"final={self.summary.get('final_candidate_targets')} verdict={self.summary.get('target_hygiene_verdict')}",
            f"exports={self.summary.get('exports')}",
        ]
        lines.append("Targets:")
        for row in self.targets[:20]:
            lines.append(
                f"- {row['market_ticker']} side={row['side']} tier={row['tier']} "
                f"replay_fills={row.get('replay_fills')} net30={_fmt(row.get('avg_net_edge_30m_cents'))} "
                f"current_spread={_fmt(row.get('current_spread_cents'))} reason={row.get('selection_reason')}"
            )
        lines.append("Actions:")
        for action in self.actions[:30]:
            lines.append(f"- {action.get('action')} {action.get('market_ticker')} {action.get('side')}: {action.get('reason')}")
        return "\n".join(lines)


class PaperMarketMakingBasket:
    """Run several tiny paper-only market-making trackers in parallel.

    This is deliberately still evidence collection, not trading. It never calls
    Kalshi order endpoints; all fills come from local trade-print data.
    """

    def __init__(self, storage: Storage | None = None, now_fn: Callable[[], datetime] | None = None):
        self.storage = storage or Storage()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.maker = PaperMarketMaker(storage=self.storage, now_fn=self.now_fn)

    def run(self, config: PaperMarketMakingBasketConfig, *, persist_exports: bool = True, once: bool = False) -> PaperMarketMakingBasketResult:
        started = self.now_fn()
        deadline = None
        if config.duration_minutes and config.duration_minutes > 0:
            deadline = started + timedelta(minutes=float(config.duration_minutes))
        targets, selector_summary = self._select_targets(config)
        refresh_deadline = started + timedelta(minutes=max(float(config.refresh_candidates_minutes), 1.0))
        last_result: PaperMarketMakingBasketResult | None = None
        while True:
            now = self.now_fn()
            if now >= refresh_deadline:
                targets, selector_summary = self._select_targets(config)
                refresh_deadline = now + timedelta(minutes=max(float(config.refresh_candidates_minutes), 1.0))
            last_result = self.run_once(config, targets=targets, selector_summary=selector_summary, persist_exports=persist_exports)
            if once or deadline is None or now >= deadline:
                return last_result
            _print_progress(last_result)
            time.sleep(max(1, int(config.interval_seconds)))

    def run_once(
        self,
        config: PaperMarketMakingBasketConfig,
        *,
        targets: list[dict[str, Any]] | None = None,
        selector_summary: dict[str, Any] | None = None,
        persist_exports: bool = True,
    ) -> PaperMarketMakingBasketResult:
        self.storage.init_db()
        if targets is None or selector_summary is None:
            targets, selector_summary = self._select_targets(config)
        target_results: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        for target in targets:
            paper_cfg = PaperMarketMakerConfig(
                market_ticker=target["market_ticker"],
                side=target["side"],
                quantity=config.quantity,
                max_position=config.max_position,
                max_open_quotes=config.max_open_quotes,
                improve_cents=config.improve_cents,
                min_spread_cents=config.min_spread_cents,
                min_depth=config.min_depth,
                quote_ttl_seconds=config.quote_ttl_seconds,
                stale_orderbook_seconds=config.stale_orderbook_seconds,
                interval_seconds=config.interval_seconds,
                duration_minutes=None,
                dry_run=config.dry_run,
            )
            result = self.maker.run_once(paper_cfg, persist_exports=False)
            target_results.append(result.summary)
            for action in result.actions:
                actions.append(
                    {
                        **action,
                        "market_ticker": target["market_ticker"],
                        "side": target["side"],
                        "tier": target["tier"],
                    }
                )
        summary = self._summary(config, targets, target_results, selector_summary)
        if persist_exports:
            summary["exports"] = _export(summary, targets, target_results, actions)
        else:
            summary["exports"] = None
        return PaperMarketMakingBasketResult(summary=summary, targets=targets, target_results=target_results, actions=actions)

    def _select_targets(self, config: PaperMarketMakingBasketConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        replay_config = MarketMakingReplayConfig(
            quantity=config.quantity,
            max_position=config.max_position,
            max_open_quotes=config.max_open_quotes,
            improve_cents=config.improve_cents,
            min_spread_cents=config.min_spread_cents,
            min_depth=config.min_depth,
            quote_ttl_seconds=config.quote_ttl_seconds,
            quote_spacing_seconds=config.quote_spacing_seconds,
            stale_current_seconds=config.stale_orderbook_seconds,
            require_current_setup=True,
            weather_only=config.weather_only,
        )
        replay = MarketMakingReplayBacktester(storage=self.storage, config=replay_config).replay(
            last_days=config.last_days,
            max_markets=config.search_max_markets,
            persist_exports=False,
        )
        strict: list[dict[str, Any]] = []
        exploratory: list[dict[str, Any]] = []
        raw_candidate_targets = 0
        expired_or_stale_removed: list[str] = []
        for row in replay.markets:
            if not row.get("current_setup_ok"):
                continue
            raw_candidate_targets += 1
            if row.get("market_likely_expired"):
                expired_or_stale_removed.append(str(row.get("market_ticker")))
                continue
            replay_fills = int(row.get("fills") or 0)
            net_30 = float(row.get("avg_net_edge_30m_cents") or 0.0)
            adverse = float(row.get("adverse_fill_rate_30m") or 0.0)
            trades = int(row.get("trades") or 0)
            target = _target_from_replay_row(row)
            if replay_fills >= config.min_replay_fills and net_30 > 0 and adverse <= 0.35:
                target["tier"] = "REPLAY_SUPPORTED"
                target["selection_reason"] = "Current book qualifies and replay has positive trade-print fill evidence."
                strict.append(target)
            elif config.include_exploratory and trades >= config.min_recent_trades:
                target["tier"] = "EXPLORATORY_CURRENT"
                target["selection_reason"] = "Current book qualifies; included to gather paper fill evidence faster."
                exploratory.append(target)
        selected = _dedupe_targets(strict + exploratory)[: max(int(config.max_targets), 0)]
        selector_summary = dict(replay.summary)
        selector_summary["strict_selected"] = len([row for row in selected if row["tier"] == "REPLAY_SUPPORTED"])
        selector_summary["exploratory_selected"] = len([row for row in selected if row["tier"] == "EXPLORATORY_CURRENT"])
        selector_summary["raw_candidate_targets"] = int(raw_candidate_targets)
        expired_removed_count = int(len(set(expired_or_stale_removed)))
        selector_summary["expired_or_stale_targets_removed"] = expired_removed_count
        selector_summary["survived_expiry_filter"] = max(0, int(raw_candidate_targets) - expired_removed_count)
        selector_summary["final_candidate_targets"] = int(len(selected))
        selector_summary["expired_target_tickers_removed"] = sorted(set(expired_or_stale_removed))
        if raw_candidate_targets == 0:
            selector_summary["target_hygiene_verdict"] = "NO_RAW_CANDIDATES"
        elif raw_candidate_targets > 0 and not selected:
            selector_summary["target_hygiene_verdict"] = "NO_VALID_TARGETS_AFTER_EXPIRY_FILTER"
        else:
            selector_summary["target_hygiene_verdict"] = "TARGET_HYGIENE_OK"
        return selected, selector_summary

    def _summary(
        self,
        config: PaperMarketMakingBasketConfig,
        targets: list[dict[str, Any]],
        target_results: list[dict[str, Any]],
        selector_summary: dict[str, Any],
    ) -> dict[str, Any]:
        quotes_total = sum(int(row.get("quotes_total") or 0) for row in target_results)
        open_quotes = sum(int(row.get("open_quotes") or 0) for row in target_results)
        filled_quotes = sum(int(row.get("filled_quotes") or 0) for row in target_results)
        cancelled_quotes = sum(int(row.get("cancelled_quotes") or 0) for row in target_results)
        edge_values: list[float] = []
        adverse_count = 0
        future_n = 0
        for row in target_results:
            obs = int(row.get("future_edge_30m_observations") or 0)
            avg_edge = _num(row.get("avg_future_edge_30m_cents"))
            if obs and avg_edge is not None:
                edge_values.extend([avg_edge] * obs)
                adverse_count += int(round(float(row.get("adverse_fill_rate_30m") or 0.0) * obs))
                future_n += obs
        avg_edge = sum(edge_values) / len(edge_values) if edge_values else 0.0
        strict = len([row for row in targets if row["tier"] == "REPLAY_SUPPORTED"])
        exploratory = len([row for row in targets if row["tier"] == "EXPLORATORY_CURRENT"])
        status = _basket_status(targets, quotes_total, filled_quotes, open_quotes)
        return {
            "status": status,
            "message": _basket_message(status),
            "weather_only": bool(config.weather_only),
            "targets": len(targets),
            "strict_targets": strict,
            "exploratory_targets": exploratory,
            "quotes_total": quotes_total,
            "open_quotes": open_quotes,
            "filled_quotes": filled_quotes,
            "cancelled_quotes": cancelled_quotes,
            "fill_rate": float(filled_quotes / max(quotes_total, 1)),
            "avg_future_edge_30m_cents": float(avg_edge),
            "future_edge_30m_observations": int(future_n),
            "adverse_fill_rate_30m": float(adverse_count / future_n) if future_n else 0.0,
            "selector_verdict": selector_summary.get("verdict"),
            "selector_fills": selector_summary.get("fills"),
            "selector_current_targets": selector_summary.get("current_paper_targets"),
            "selector_replay_supported_current_targets": selector_summary.get("replay_supported_current_targets"),
            "raw_candidate_targets": selector_summary.get("raw_candidate_targets", 0),
            "expired_or_stale_targets_removed": selector_summary.get("expired_or_stale_targets_removed", 0),
            "survived_expiry_filter": selector_summary.get("survived_expiry_filter", 0),
            "final_candidate_targets": selector_summary.get("final_candidate_targets", len(targets)),
            "expired_target_tickers_removed": selector_summary.get("expired_target_tickers_removed", []),
            "target_hygiene_verdict": selector_summary.get("target_hygiene_verdict"),
            "config": config.__dict__,
        }


def _target_from_replay_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_ticker": str(row["market_ticker"]),
        "side": str(row["best_side"]),
        "tier": "",
        "replay_fills": int(row.get("fills") or 0),
        "replay_quotes": int(row.get("quotes_opened") or 0),
        "replay_fill_rate": float(row.get("fill_rate") or 0.0),
        "avg_net_edge_30m_cents": float(row.get("avg_net_edge_30m_cents") or 0.0),
        "adverse_fill_rate_30m": float(row.get("adverse_fill_rate_30m") or 0.0),
        "score": float(row.get("score") or 0.0),
        "trades": int(row.get("trades") or 0),
        "current_spread_cents": _num(row.get("current_spread_cents")),
        "current_limit_price_cents": _num(row.get("current_limit_price_cents")),
        "current_setup_reason": row.get("current_setup_reason"),
        "market_likely_expired": bool(row.get("market_likely_expired")),
        "selection_reason": "",
    }


def _dedupe_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    selected: list[dict[str, Any]] = []
    for row in rows:
        key = (row["market_ticker"], row["side"])
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def _basket_status(targets: list[dict[str, Any]], quotes_total: int, filled_quotes: int, open_quotes: int) -> str:
    if not targets:
        return "PAPER_BASKET_NO_TARGETS"
    if filled_quotes > 0:
        return "PAPER_BASKET_COLLECTING_FILLS"
    if quotes_total > 0 or open_quotes > 0:
        return "PAPER_BASKET_ACTIVE_NO_FILLS_YET"
    return "PAPER_BASKET_WAITING_FOR_SETUP"


def _basket_message(status: str) -> str:
    if status == "PAPER_BASKET_NO_TARGETS":
        return "No current paper targets found; collect more data or widen search settings."
    if status == "PAPER_BASKET_COLLECTING_FILLS":
        return "Basket has paper fills; continue paper-only monitoring and inspect markouts."
    if status == "PAPER_BASKET_ACTIVE_NO_FILLS_YET":
        return "Basket is quoting paper targets, but no trade-print fills yet."
    return "Basket is alive but current filters prevented paper quotes."


def _export(summary: dict[str, Any], targets: list[dict[str, Any]], target_results: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    targets_path = reports / "paper_market_making_basket_targets.csv"
    summaries_path = reports / "paper_market_making_basket_target_summaries.csv"
    actions_path = reports / "paper_market_making_basket_actions.csv"
    summary_path = reports / "paper_market_making_basket_summary.json"
    pd.DataFrame(targets).to_csv(targets_path, index=False)
    pd.DataFrame(target_results).to_csv(summaries_path, index=False)
    pd.DataFrame(actions).to_csv(actions_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return {
        "targets": str(targets_path),
        "target_summaries": str(summaries_path),
        "actions": str(actions_path),
        "summary": str(summary_path),
    }


def _print_progress(result: PaperMarketMakingBasketResult) -> None:
    summary = result.summary
    print(
        "PAPER_MM_BASKET HEARTBEAT "
        f"status={summary.get('status')} targets={summary.get('targets')} "
        f"strict={summary.get('strict_targets')} exploratory={summary.get('exploratory_targets')} "
        f"quotes={summary.get('quotes_total')} open={summary.get('open_quotes')} "
        f"filled={summary.get('filled_quotes')} cancelled={summary.get('cancelled_quotes')} "
        f"avg_edge30={_fmt(summary.get('avg_future_edge_30m_cents'))}",
        flush=True,
    )


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _num(value)
    return "none" if number is None else f"{number:.2f}"
