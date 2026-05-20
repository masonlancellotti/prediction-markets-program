from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import PROJECT_ROOT
from data.storage import Storage
from data.weather_settlement_loader import SETTLEMENT_VERSION
from parsing.weather_contract import PARSER_VERSION
from research.liquidity_analysis import LiquidityAnalyzer
from research.signal_validation import SignalValidator


@dataclass(frozen=True)
class TradingReadinessResult:
    status: str
    message: str
    reasons: list[str]
    metrics: dict[str, Any]
    next_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "next_command": self.next_command,
        }

    def to_text(self) -> str:
        lines = [f"Trading readiness: {self.status}", self.message, "Reasons:"]
        lines.extend(f"- {reason}" for reason in self.reasons)
        lines.append(f"Next command: {self.next_command}")
        return "\n".join(lines)


class TradingReadiness:
    def __init__(self, storage: Storage | None = None):
        self.storage = storage or Storage()

    def evaluate(self, last_days: int = 7) -> TradingReadinessResult:
        since = (date.today() - timedelta(days=max(last_days, 1))).isoformat()
        metrics = {
            "orderbook_snapshots": self._count_since("orderbook_snapshots_live", "ts", since),
            "weather_observation_rows": self._count_since("weather_observation_snapshots_live", "ts_recorded", since),
            "weather_forecast_rows": self._count_since("weather_forecast_snapshots_live", "ts_recorded", since),
            "replay_rows": self._count_since("recorded_orderbook_replay_snapshots", "ts", since),
            "settlement_labels_primary": self._primary_settlement_count(),
            "stale_runs": self._stale_count(),
            "parser_version": PARSER_VERSION,
            "settlement_version": SETTLEMENT_VERSION,
        }
        sweeps = self._latest_sweeps(since)
        validation = SignalValidator(self.storage).validate(last_days=last_days).summary_by_strategy
        liquidity = LiquidityAnalyzer(self.storage).analyze(last_days=last_days, persist_exports=False).summary
        mm_summary = _load_market_making_summary()
        metrics["latest_sweeps"] = sweeps[:10]
        metrics["clean_sweep_count"] = len(sweeps)
        metrics["signal_validation"] = validation
        metrics["liquidity"] = liquidity
        metrics["market_making_verdict"] = mm_summary.get("market_making_verdict")
        metrics["market_making_paper_watchlist_candidates"] = int(mm_summary.get("paper_watchlist_candidates") or 0)
        metrics["market_making_trade_evidence_fills"] = int(mm_summary.get("trade_evidence_fills") or 0)
        metrics["market_making_summary_age_hours"] = mm_summary.get("_age_hours")
        reasons: list[str] = []
        if metrics["orderbook_snapshots"] < 1000:
            reasons.append("Not enough recorded orderbook snapshots.")
        if metrics["weather_forecast_rows"] == 0:
            reasons.append("No recorded forecast snapshots; late-day forecast-aware testing is weak.")
        if metrics["replay_rows"] == 0:
            reasons.append("No recorded replay rows. Build replay after settlements exist.")
        if metrics["settlement_labels_primary"] == 0:
            reasons.append("No primary high-confidence settlement labels.")
        if metrics["stale_runs"] > 0:
            reasons.append(f"Stale runs exist ({metrics['stale_runs']} stale, {len(sweeps)} clean current-version sweeps); dashboard/report must exclude stale runs.")
        best = sweeps[0] if sweeps else None
        if not best:
            reasons.append("No clean recorded strategy sweep results.")
        elif float(best.get("net_pnl") or 0.0) <= 0:
            reasons.append("Best clean sweep does not have positive net P&L.")
        elif int(best.get("fills") or 0) < 30:
            reasons.append("Best clean sweep has fewer than 30 fills.")
        if liquidity.get("passive_verdict") not in {"PAPER_READY_SPECIFIC_STRATEGY", "PAPER_CANDIDATE_APPROX_FILLS"}:
            reasons.append("Passive liquidity has not shown reliable fill/anti-adverse-selection evidence.")
        if liquidity.get("passive_verdict") == "PAPER_CANDIDATE_APPROX_FILLS":
            reasons.append("Passive liquidity fill candidates are approximate (touch/replay model, not trade prints). Verify with analyze-market-making.")
        if validation and not any(item.get("recommendation") == "interesting_signal" for item in validation):
            reasons.append("Signals have not clearly beaten future prices.")
        mm_verdict = mm_summary.get("market_making_verdict")
        mm_candidates = int(mm_summary.get("paper_watchlist_candidates") or 0)
        if mm_verdict:
            age_h = mm_summary.get("_age_hours")
            age_str = f", summary age={age_h:.1f}h" if age_h is not None else ""
            if mm_candidates > 0:
                watchlist = mm_summary.get("paper_watchlist_tickers") or []
                top = watchlist[0] if watchlist else None
                if top and not top.get("market_likely_expired"):
                    ticker_str = (
                        f" Top: {top['market_ticker']} {top['best_side']}"
                        f" fills={top['trade_evidence_fills']}"
                        f" edge_net={top['avg_edge_after_penalty_30m_cents']:.1f}c."
                    )
                elif top and top.get("market_likely_expired"):
                    ticker_str = f" Top candidate {top['market_ticker']} may be expired — re-run analyze-market-making."
                else:
                    ticker_str = ""
                reasons.append(
                    f"Market-making track: {mm_candidates} paper watchlist candidate(s) (verdict={mm_verdict}{age_str}).{ticker_str}"
                    f" Run paper-market-making-basket to gather fills across several current candidates."
                )
            else:
                reasons.append(f"Market-making track: {mm_verdict} — no paper watchlist candidates yet{age_str}.")
        status = "NOT_READY_DATA_INCOMPLETE"
        next_command = "python main.py build-recorded-replay --last-days 7"
        if metrics["replay_rows"] > 0 and best is None:
            status = "NOT_READY_ANALYSIS_NOT_RUN"
            next_command = "python main.py sweep-recorded --last-days 7"
        elif best and float(best.get("net_pnl") or 0.0) <= 0:
            status = "NOT_READY_NO_EDGE"
            next_command = "python main.py analyze-liquidity --last-days 7"
        elif best and int(best.get("fills") or 0) < 30 and float(best.get("net_pnl") or 0.0) > 0:
            status = "RESEARCH_READY_MORE_DATA_NEEDED"
            next_command = "python main.py edge-report --last-days 7"
        if _paper_ready(best, validation, liquidity):
            status = "PAPER_READY_SPECIFIC_STRATEGY"
            next_command = "python main.py paper-trade --strategy " + str(best.get("strategy")) + " --weather-only"
        if _tiny_live_ready(best, validation, liquidity):
            status = "TINY_LIVE_READY_SPECIFIC_STRATEGY"
            next_command = "Do not run live trading from this repo yet; manual approval and paper results required."
        if status in {"NOT_READY_NO_EDGE", "NOT_READY_ANALYSIS_NOT_RUN", "NOT_READY_DATA_INCOMPLETE"} \
                and mm_candidates > 0 and mm_verdict == "PAPER_WATCHLIST_CANDIDATES":
            mm_age = mm_summary.get("_age_hours")
            watchlist_for_cmd = mm_summary.get("paper_watchlist_tickers")  # None means old JSON without field
            all_expired = (
                watchlist_for_cmd is not None
                and len(watchlist_for_cmd) > 0
                and all(t.get("market_likely_expired") for t in watchlist_for_cmd)
            )
            if mm_age is None or mm_age > 6.0 or all_expired:
                next_command = "python main.py analyze-market-making --last-days 7"
            else:
                live_candidates = [t for t in (watchlist_for_cmd or []) if not t.get("market_likely_expired")]
                if live_candidates:
                    next_command = _paper_market_making_basket_command()
                else:
                    next_command = _paper_market_making_basket_command()
        message = _message(status)
        if status == "TINY_LIVE_READY_SPECIFIC_STRATEGY":
            reasons.append("This status should be rare; verify paper/live gates manually before any real money.")
        return TradingReadinessResult(status, message, reasons, metrics, next_command)

    def _count_since(self, table: str, column: str, since: str) -> int:
        frame = self.storage.fetch_sql(f"SELECT COUNT(*) AS count FROM {table} WHERE date({column}) >= :since", {"since": since})
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def _primary_settlement_count(self) -> int:
        frame = self.storage.fetch_sql("SELECT COUNT(*) AS count FROM settlement_labels WHERE confidence >= 0.85")
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def _stale_count(self) -> int:
        frame = self.storage.fetch_sql("SELECT COUNT(*) AS count FROM backtest_runs WHERE COALESCE(is_stale, 0) = 1")
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def _latest_sweeps(self, since: str) -> list[dict[str, Any]]:
        frame = self.storage.fetch_sql(
            """
            SELECT * FROM recorded_strategy_sweeps
            WHERE COALESCE(is_stale, 0) = 0
              AND COALESCE(parser_version, '') = :parser_version
              AND COALESCE(settlement_version, '') = :settlement_version
              AND date(ts) >= :since
            ORDER BY net_pnl DESC, fills DESC
            LIMIT 25
            """,
            {"parser_version": PARSER_VERSION, "settlement_version": SETTLEMENT_VERSION, "since": since},
        )
        return [] if frame.empty else frame.to_dict("records")


def _paper_ready(best: dict[str, Any] | None, validation: list[dict[str, Any]], liquidity: dict[str, Any]) -> bool:
    if not best:
        return False
    if int(best.get("fills") or 0) < 30 or float(best.get("net_pnl") or 0.0) <= 0:
        return False
    if "passive" in str(best.get("strategy")) and liquidity.get("passive_verdict") not in {"PAPER_READY_SPECIFIC_STRATEGY", "PAPER_CANDIDATE_APPROX_FILLS"}:
        return False
    return any(item.get("strategy") == best.get("strategy") and item.get("recommendation") == "interesting_signal" for item in validation)


def _tiny_live_ready(best: dict[str, Any] | None, validation: list[dict[str, Any]], liquidity: dict[str, Any]) -> bool:
    if not _paper_ready(best, validation, liquidity):
        return False
    if int(best.get("fills") or 0) < 100:
        return False
    # Live paper results are not yet integrated, so this must remain false.
    return False


def _load_market_making_summary(path: Path | None = None) -> dict[str, Any]:
    try:
        p = path or (PROJECT_ROOT / "reports" / "market_making_summary.json")
        if not p.exists():
            return {}
        age_hours = (time.time() - p.stat().st_mtime) / 3600.0
        data = json.loads(p.read_text(encoding="utf-8"))
        data["_age_hours"] = age_hours
        return data
    except Exception:
        return {}


def _paper_market_making_basket_command() -> str:
    return (
        "python main.py paper-market-making-basket --last-days 1 "
        "--search-max-markets 100 --max-targets 5 --duration-minutes 60 "
        "--quantity 1 --max-position 5 --max-open-quotes 1"
    )


def _message(status: str) -> str:
    return {
        "NOT_READY_DATA_INCOMPLETE": "Do not trade. Data collection or replay labels are incomplete.",
        "NOT_READY_ANALYSIS_NOT_RUN": "Do not trade. Replay exists, but clean strategy sweeps have not been run yet.",
        "NOT_READY_NO_EDGE": "Do not trade. No clean strategy has survived replay after current parser/settlement versions.",
        "RESEARCH_READY_MORE_DATA_NEEDED": "Research can continue, but sample size/robustness is not enough for paper confidence.",
        "PAPER_READY_SPECIFIC_STRATEGY": "Paper testing may be justified for one specific strategy, after manual candidate review.",
        "TINY_LIVE_READY_SPECIFIC_STRATEGY": "Tiny live readiness gate appears satisfied, but live trading is still disabled and requires manual approval.",
    }.get(status, "Unknown readiness status.")
