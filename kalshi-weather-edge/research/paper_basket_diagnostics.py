from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from config import PROJECT_ROOT


@dataclass(frozen=True)
class PaperBasketDiagnosticsResult:
    summary: dict[str, Any]

    def to_text(self) -> str:
        lines = [
            f"paper_basket_diagnostics_status={self.summary.get('diagnostics_status')}",
            f"source_summary={self.summary.get('source_files', {}).get('summary')}",
            f"basket_status={self.summary.get('status')} target_hygiene={self.summary.get('target_hygiene_verdict')}",
            f"targets strict={self.summary.get('strict_targets')} exploratory={self.summary.get('exploratory_targets')} "
            f"final={self.summary.get('targets')} raw={self.summary.get('raw_candidate_targets')} survived={self.summary.get('survived_expiry_filter')}",
            f"quotes opened={self.summary.get('total_quotes_opened')} cancelled={self.summary.get('total_quotes_cancelled')} "
            f"fills_seen={self.summary.get('total_trade_print_fills_seen')} final_filled={self.summary.get('final_filled_quotes')}",
            f"fill_seen_in_history_but_not_final={str(self.summary.get('fill_seen_in_history_but_not_final')).lower()}",
            f"spread_below_minimum={self.summary.get('spread_below_minimum_count')}",
            f"recommendation={self.summary.get('primary_recommendation')}",
        ]
        lines.append("Quote rejection breakdown:")
        for key, value in sorted((self.summary.get("quote_rejection_breakdown") or {}).items()):
            lines.append(f"- {key}: {value}")
        lines.append("Quote blocking diagnostics:")
        blocking = self.summary.get("quote_blocking_diagnostics") or {}
        lines.append(f"- max_open_quotes_reached_dominant: {str(blocking.get('max_open_quotes_reached_dominant')).lower()}")
        lines.append(f"- open_quotes_blocking_refresh: {str(blocking.get('open_quotes_blocking_refresh')).lower()}")
        lines.append(f"- max_open_quotes_reached_by_target: {blocking.get('max_open_quotes_reached_by_target')}")
        lines.append(f"- cancel_reason_breakdown: {blocking.get('cancel_reason_breakdown')}")
        lines.append("Target persistence diagnostics:")
        for row in self.summary.get("target_persistence_diagnostics") or []:
            lines.append(
                f"- {row.get('market_ticker')} {row.get('side')} tier={row.get('tier')} "
                f"lifetime_seconds={row.get('strict_target_lifetime_seconds')} removed_reason={row.get('target_removed_reason')}"
            )
        lines.append("Targets with any fill:")
        for row in self.summary.get("targets_with_any_fill") or []:
            lines.append(f"- {row.get('market_ticker')} {row.get('side')} tier={row.get('tier')}")
        lines.append("Final targets:")
        for row in self.summary.get("targets_final") or []:
            lines.append(f"- {row.get('market_ticker')} {row.get('side')} tier={row.get('tier')}")
        lines.append("Suggested next paper settings:")
        for item in self.summary.get("suggested_next_settings") or []:
            lines.append(f"- {item}")
        return "\n".join(lines)


class PaperBasketDiagnosticsReporter:
    """Read-only diagnostics over the latest exported paper basket files."""

    def __init__(self, reports_dir: Path | None = None):
        self.reports_dir = reports_dir or (PROJECT_ROOT / "reports")

    def build(self) -> PaperBasketDiagnosticsResult:
        paths = _paths(self.reports_dir)
        missing = [name for name, path in paths.items() if not path.exists()]
        if missing:
            return PaperBasketDiagnosticsResult(
                {
                    "diagnostics_status": "PAPER_BASKET_DIAGNOSTICS_MISSING_EXPORTS",
                    "message": f"Missing paper basket export file(s): {', '.join(missing)}",
                    "missing_exports": missing,
                    "source_files": {name: str(path) for name, path in paths.items()},
                    "research_only": True,
                    "readiness_promotion": "none",
                }
            )
        summary = _read_json(paths["summary"])
        actions = _read_csv(paths["actions"])
        targets = _read_csv(paths["targets"])
        target_summaries = _read_csv(paths["target_summaries"])
        diagnostics = _diagnose(summary, actions, targets, target_summaries)
        diagnostics["source_files"] = {name: str(path) for name, path in paths.items()}
        diagnostics["diagnostics_status"] = "PAPER_BASKET_DIAGNOSTICS_OK"
        diagnostics["research_only"] = True
        diagnostics["readiness_promotion"] = "none"
        return PaperBasketDiagnosticsResult(diagnostics)


def _paths(reports_dir: Path) -> dict[str, Path]:
    return {
        "summary": reports_dir / "paper_market_making_basket_summary.json",
        "actions": reports_dir / "paper_market_making_basket_actions.csv",
        "targets": reports_dir / "paper_market_making_basket_targets.csv",
        "target_summaries": reports_dir / "paper_market_making_basket_target_summaries.csv",
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _diagnose(summary: dict[str, Any], actions: pd.DataFrame, targets: pd.DataFrame, target_summaries: pd.DataFrame) -> dict[str, Any]:
    rejection_breakdown = _merged_rejection_breakdown(summary, actions)
    strict_targets = _count_tier(targets, "REPLAY_SUPPORTED", fallback=summary.get("strict_targets"))
    exploratory_targets = _count_tier(targets, "EXPLORATORY_CURRENT", fallback=summary.get("exploratory_targets"))
    targets_with_fill = _targets_with_fill(summary, target_summaries)
    final_targets = _targets_final(summary, targets)
    total_quotes_opened = _int(summary.get("total_quotes_opened"), fallback=_count_action(actions, "QUOTE_OPENED"))
    total_quotes_cancelled = _int(summary.get("total_quotes_cancelled"), fallback=_count_action(actions, "QUOTE_CANCELLED"))
    total_fills_seen = _int(summary.get("total_trade_print_fills_seen"), fallback=_count_action(actions, "QUOTE_FILLED"))
    final_filled = _int(summary.get("final_filled_quotes"), fallback=_int(summary.get("filled_quotes")))
    diagnostics = {
        "status": summary.get("status"),
        "message": summary.get("message"),
        "target_hygiene_verdict": summary.get("target_hygiene_verdict"),
        "raw_candidate_targets": _int(summary.get("raw_candidate_targets")),
        "survived_expiry_filter": _int(summary.get("survived_expiry_filter")),
        "targets": _int(summary.get("targets"), fallback=len(targets)),
        "strict_targets": strict_targets,
        "exploratory_targets": exploratory_targets,
        "quotes_total": _int(summary.get("quotes_total"), fallback=len(actions)),
        "open_quotes": _int(summary.get("open_quotes")),
        "filled_quotes": _int(summary.get("filled_quotes")),
        "cancelled_quotes": _int(summary.get("cancelled_quotes")),
        "total_quotes_opened": total_quotes_opened,
        "total_quotes_cancelled": total_quotes_cancelled,
        "total_trade_print_fills_seen": total_fills_seen,
        "final_open_quotes": _int(summary.get("final_open_quotes"), fallback=_int(summary.get("open_quotes"))),
        "final_filled_quotes": final_filled,
        "final_cancelled_quotes": _int(summary.get("final_cancelled_quotes"), fallback=_int(summary.get("cancelled_quotes"))),
        "fill_seen_in_history_but_not_final": bool(summary.get("fill_seen_in_history_but_not_final") or total_fills_seen > final_filled),
        "quote_rejection_breakdown": rejection_breakdown,
        "spread_below_minimum_count": _int(rejection_breakdown.get("spread_below_minimum")),
        "targets_with_any_fill": targets_with_fill,
        "targets_final": final_targets,
        "targets_seen_over_run": summary.get("targets_seen_over_run") or final_targets,
        "targets_removed_reason": summary.get("targets_removed_reason") or {},
        "target_persistence_diagnostics": _target_persistence(summary),
        "quote_blocking_diagnostics": _quote_blocking(actions, rejection_breakdown),
        "latest_actions": _latest_actions(actions),
        "suggested_next_settings": [],
        "primary_recommendation": "",
        "input_row_counts": {
            "actions": int(len(actions)),
            "targets": int(len(targets)),
            "target_summaries": int(len(target_summaries)),
        },
    }
    suggestions = _recommend(diagnostics)
    diagnostics["suggested_next_settings"] = suggestions
    diagnostics["primary_recommendation"] = suggestions[0] if suggestions else "Keep current settings and gather more exported evidence before changing parameters."
    return diagnostics


def _merged_rejection_breakdown(summary: dict[str, Any], actions: pd.DataFrame) -> dict[str, int]:
    base = {
        "spread_below_minimum": 0,
        "max_open_quotes_reached": 0,
        "target_removed": 0,
        "stale_or_expired_target": 0,
        "no_valid_target": 0,
        "other": 0,
    }
    existing = summary.get("quote_rejection_breakdown") or {}
    for key in base:
        base[key] = _int(existing.get(key))
    if any(value > 0 for value in base.values()):
        if _int(summary.get("targets")) == 0:
            base["no_valid_target"] = max(base["no_valid_target"], 1)
        return base
    if not actions.empty and "action" in actions.columns:
        no_quotes = actions[actions["action"].astype(str) == "NO_QUOTE"]
        for _, row in no_quotes.iterrows():
            bucket = _quote_rejection_bucket(str(row.get("reason") or ""))
            base[bucket] = base.get(bucket, 0) + 1
    if _int(summary.get("targets")) == 0:
        base["no_valid_target"] = max(base["no_valid_target"], 1)
    return base


def _quote_rejection_bucket(reason: str) -> str:
    text = reason.lower()
    if "spread" in text and "below minimum" in text:
        return "spread_below_minimum"
    if "already has" in text and "open paper quote" in text:
        return "max_open_quotes_reached"
    if "stale" in text or "expired" in text or "market status" in text:
        return "stale_or_expired_target"
    return "other"


def _recommend(diagnostics: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    strict = _int(diagnostics.get("strict_targets"))
    exploratory = _int(diagnostics.get("exploratory_targets"))
    spread_rejects = _int(diagnostics.get("spread_below_minimum_count"))
    total_rejects = sum(_int(value) for value in (diagnostics.get("quote_rejection_breakdown") or {}).values())
    fills_seen = _int(diagnostics.get("total_trade_print_fills_seen"))
    final_fills = _int(diagnostics.get("final_filled_quotes"))
    blocking = diagnostics.get("quote_blocking_diagnostics") or {}
    quick_removed = [
        row
        for row in diagnostics.get("target_persistence_diagnostics") or []
        if row.get("target_removed_after_seconds") is not None and float(row.get("target_removed_after_seconds") or 0.0) <= 900
    ]
    if blocking.get("max_open_quotes_reached_dominant"):
        suggestions.append("Max-open-quote rejections dominate; test max_open_quotes=2 next before lowering spread thresholds.")
    if diagnostics.get("fill_seen_in_history_but_not_final"):
        suggestions.append("Increase duration or review target rotation; fills appeared earlier but the final target state had fewer/no fills.")
    if strict == 0 and exploratory > 0:
        suggestions.append("Collect more replay trade evidence before requiring strict-only targets; the final basket was exploratory.")
    elif strict > 0 and exploratory > strict:
        suggestions.append("Consider requiring strict-only for the next diagnostic run if the goal is cleaner evidence over faster fill discovery.")
    if spread_rejects > 0 and spread_rejects >= max(1, total_rejects // 2):
        suggestions.append("Spread-below-minimum rejections dominate; collect more data or wait for wider spreads before lowering thresholds.")
    if quick_removed:
        suggestions.append("Targets disappeared quickly; use shorter diagnostic runs or more frequent target-refresh diagnostics before changing thresholds.")
    if _int(diagnostics.get("targets")) >= 5 and fills_seen == 0:
        suggestions.append("Do not raise max targets yet; collect more trade evidence or improve target quality first.")
    elif _int(diagnostics.get("targets")) < 3 and spread_rejects == 0:
        suggestions.append("A slightly higher max-targets cap may help only if target hygiene remains clean and strict candidates exist.")
    if fills_seen == 0:
        suggestions.append("Collect more trade evidence first; this export shows no paper trade-print fills across the run.")
    elif final_fills == 0:
        suggestions.append("Do not treat final no-fill state as the full run result; use total_trade_print_fills_seen and targets_with_any_fill.")
    return suggestions or ["Keep settings unchanged for the next short diagnostic run; no dominant rejection or target-quality issue was detected."]


def _target_persistence(summary: dict[str, Any]) -> list[dict[str, Any]]:
    targets_by_key: dict[str, dict[str, Any]] = {}
    for row in summary.get("targets_seen_over_run") or []:
        targets_by_key[f"{row.get('market_ticker')}|{row.get('side')}"] = row
    for row in summary.get("targets_with_any_fill") or []:
        targets_by_key.setdefault(f"{row.get('market_ticker')}|{row.get('side')}", row)
    targets = list(targets_by_key.values())
    first_seen = summary.get("target_first_seen") or {}
    last_seen = summary.get("target_last_seen") or {}
    removed_at = summary.get("target_removed_at") or {}
    removed_after = summary.get("target_removed_after_seconds") or {}
    removed_spread = summary.get("target_removed_last_spread_cents") or {}
    removed_reason = summary.get("targets_removed_reason") or {}
    rows: list[dict[str, Any]] = []
    for row in targets:
        key = f"{row.get('market_ticker')}|{row.get('side')}"
        after = _num(removed_after.get(key))
        rows.append(
            {
                "market_ticker": row.get("market_ticker"),
                "side": row.get("side"),
                "tier": row.get("tier"),
                "target_first_seen": first_seen.get(key),
                "target_last_seen": last_seen.get(key),
                "target_removed_at": removed_at.get(key),
                "target_removed_after_seconds": after,
                "strict_target_lifetime_seconds": after if row.get("tier") == "REPLAY_SUPPORTED" else None,
                "target_removed_reason": removed_reason.get(key),
                "current_spread_at_removal": _num(removed_spread.get(key)),
                "stopped_qualifying_reason": _stopped_reason(removed_reason.get(key), _num(removed_spread.get(key))),
            }
        )
    return rows


def _quote_blocking(actions: pd.DataFrame, rejection_breakdown: dict[str, int]) -> dict[str, Any]:
    max_open_by_target: dict[str, int] = {}
    spread_by_target: dict[str, int] = {}
    cancel_reason_breakdown = {"ttl_or_no_trade_fill": 0, "target_state_or_other": 0}
    if not actions.empty:
        for _, row in actions.iterrows():
            action = str(row.get("action") or "")
            key = f"{row.get('market_ticker')}|{row.get('side')}"
            reason = str(row.get("reason") or "")
            if action == "NO_QUOTE":
                bucket = _quote_rejection_bucket(reason)
                if bucket == "max_open_quotes_reached":
                    max_open_by_target[key] = max_open_by_target.get(key, 0) + 1
                elif bucket == "spread_below_minimum":
                    spread_by_target[key] = spread_by_target.get(key, 0) + 1
            elif action == "QUOTE_CANCELLED":
                lower = reason.lower()
                if "no trade-print fill" in lower or "ttl" in lower or "after" in lower:
                    cancel_reason_breakdown["ttl_or_no_trade_fill"] += 1
                else:
                    cancel_reason_breakdown["target_state_or_other"] += 1
    total_rejections = sum(_int(value) for value in rejection_breakdown.values())
    max_open = _int(rejection_breakdown.get("max_open_quotes_reached"))
    spread = _int(rejection_breakdown.get("spread_below_minimum"))
    return {
        "max_open_quotes_reached_by_target": dict(sorted(max_open_by_target.items())),
        "spread_below_minimum_by_target": dict(sorted(spread_by_target.items())),
        "max_open_quotes_reached_dominant": bool(total_rejections and max_open > total_rejections / 2),
        "spread_below_minimum_dominant": bool(total_rejections and spread > total_rejections / 2),
        "open_quotes_blocking_refresh": bool(max_open > 0),
        "max_open_quotes_1_may_be_restrictive": bool(max_open > 0),
        "cancel_reason_breakdown": cancel_reason_breakdown,
    }


def _stopped_reason(reason: Any, spread: float | None) -> str | None:
    text = str(reason or "").lower()
    if "selector" in text:
        return "selector_refresh_or_current_target_filter"
    if spread is not None and spread < 8.0:
        return "spread_below_minimum"
    if "stale" in text or "expired" in text:
        return "stale_or_expired"
    return None if not reason else str(reason)


def _count_tier(frame: pd.DataFrame, tier: str, fallback: Any = 0) -> int:
    if frame.empty or "tier" not in frame.columns:
        return _int(fallback)
    return int((frame["tier"].astype(str) == tier).sum())


def _count_action(frame: pd.DataFrame, action: str) -> int:
    if frame.empty or "action" not in frame.columns:
        return 0
    return int((frame["action"].astype(str) == action).sum())


def _targets_with_fill(summary: dict[str, Any], target_summaries: pd.DataFrame) -> list[dict[str, str]]:
    existing = summary.get("targets_with_any_fill")
    if isinstance(existing, list) and existing:
        return existing
    if target_summaries.empty or "filled_quotes" not in target_summaries.columns:
        return []
    rows: list[dict[str, str]] = []
    for _, row in target_summaries.iterrows():
        if _int(row.get("filled_quotes")) <= 0:
            continue
        rows.append(
            {
                "market_ticker": str(row.get("market_ticker")),
                "side": str(row.get("side")),
                "tier": str(row.get("tier") or ""),
            }
        )
    return rows


def _targets_final(summary: dict[str, Any], targets: pd.DataFrame) -> list[dict[str, str]]:
    existing = summary.get("targets_final")
    if isinstance(existing, list) and existing:
        return existing
    rows: list[dict[str, str]] = []
    for _, row in targets.iterrows():
        rows.append(
            {
                "market_ticker": str(row.get("market_ticker")),
                "side": str(row.get("side")),
                "tier": str(row.get("tier") or ""),
            }
        )
    return rows


def _latest_actions(actions: pd.DataFrame) -> list[dict[str, str]]:
    if actions.empty:
        return []
    rows: list[dict[str, str]] = []
    for _, row in actions.tail(10).iterrows():
        rows.append(
            {
                "action": str(row.get("action")),
                "market_ticker": str(row.get("market_ticker")),
                "side": str(row.get("side")),
                "reason": str(row.get("reason")),
            }
        )
    return rows


def _int(value: Any, fallback: int = 0) -> int:
    try:
        if value is None or value != value:
            return fallback
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _num(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
