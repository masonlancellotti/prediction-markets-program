from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from config import PROJECT_ROOT
from data.storage import Storage
from research.market_making_analysis import MarketMakingAnalyzer, MarketMakingConfig
from research.paper_market_making_evidence import PaperMarketMakingEvidenceConfig, PaperMarketMakingEvidenceReporter


DISCLAIMER = (
    "Paper market-making target review is research-only. It reconciles offline analyzer candidates with local paper "
    "quote evidence; it does not place trades, does not change trading readiness, and does not prove live profitability."
)

REVIEW_BUCKETS = ("CONTINUE_PAPER", "CONTINUE_SMALL_SIZE", "NEED_MORE_EVIDENCE", "DOWNGRADE", "AVOID_FOR_NOW")


@dataclass(frozen=True)
class PaperMarketMakingTargetReviewConfig:
    last_days: int = 7
    too_few_fills_threshold: int = 5
    adverse_high_threshold: float = 0.35
    adverse_caution_threshold: float = 0.20
    prefer_cached_reports: bool = True
    weather_only: bool = False


@dataclass(frozen=True)
class PaperMarketMakingTargetReviewResult:
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        counts = self.summary.get("priority_counts", {})
        count_text = " ".join(f"{bucket}={counts.get(bucket, 0)}" for bucket in REVIEW_BUCKETS)
        lines = [
            f"paper_market_making_target_review_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"rows={self.summary.get('rows')} analyzer_rows={self.summary.get('analyzer_rows')} "
            f"evidence_rows={self.summary.get('evidence_rows')} joined_rows={self.summary.get('joined_rows')}",
            f"weather_only={str(self.summary.get('weather_only')).lower()}",
            f"priority_buckets: {count_text}",
            f"exports={self.exports}",
            f"disclaimer={DISCLAIMER}",
            "Top CONTINUE_PAPER candidates:",
        ]
        for row in self.summary.get("top_continue_paper", [])[:5]:
            lines.append(
                f"- {row.get('market_ticker')} {row.get('side')} analyzer_fills={row.get('analyzer_fills')} "
                f"paper_fills={row.get('paper_fills')} paper_net30={_fmt(row.get('paper_net30'))} "
                f"paper_adverse30={_fmt(row.get('paper_adverse30'))} cautions={row.get('caution_flags')}"
            )
        lines.append("Top DOWNGRADE/AVOID candidates:")
        for row in self.summary.get("top_downgrade_or_avoid", [])[:5]:
            lines.append(
                f"- {row.get('market_ticker')} {row.get('side')} bucket={row.get('priority_bucket')} "
                f"paper_fills={row.get('paper_fills')} paper_net30={_fmt(row.get('paper_net30'))} "
                f"paper_adverse30={_fmt(row.get('paper_adverse30'))} reasons={row.get('review_reasons')}"
            )
        return "\n".join(lines)


class PaperMarketMakingTargetReviewer:
    """Read-only reconciliation of analyzer candidates and paper quote evidence."""

    def __init__(self, storage: Storage | None = None, now_fn: Callable[[], datetime] | None = None):
        self.storage = storage or Storage()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def build(
        self,
        config: PaperMarketMakingTargetReviewConfig | None = None,
        *,
        persist_exports: bool = True,
    ) -> PaperMarketMakingTargetReviewResult:
        config = config or PaperMarketMakingTargetReviewConfig()
        if config.prefer_cached_reports and not config.weather_only:
            cached = _load_cached_inputs()
            if cached is not None:
                analyzer_markets, evidence_rows, analyzer_summary, evidence_summary = cached
                return build_target_review(
                    analyzer_markets=analyzer_markets,
                    evidence_rows=evidence_rows,
                    analyzer_summary={**analyzer_summary, "target_review_input_source": "cached_reports"},
                    evidence_summary={**evidence_summary, "target_review_input_source": "cached_reports"},
                    config=config,
                    generated_at=self.now_fn(),
                    persist_exports=persist_exports,
                )
        self.storage.init_db()
        analyzer = MarketMakingAnalyzer(
            storage=self.storage,
            config=MarketMakingConfig(weather_only=config.weather_only),
        ).analyze(last_days=config.last_days, persist_exports=False)
        evidence = PaperMarketMakingEvidenceReporter(storage=self.storage, now_fn=self.now_fn).build(
            PaperMarketMakingEvidenceConfig(
                last_days=config.last_days,
                too_few_fills_threshold=config.too_few_fills_threshold,
                adverse_high_threshold=config.adverse_high_threshold,
            ),
            persist_exports=False,
        )
        evidence_rows = evidence.rows
        if config.weather_only:
            weather_tickers = _weather_tickers(self.storage)
            evidence_rows = [row for row in evidence_rows if str(row.get("market_ticker") or "") in weather_tickers]
        return build_target_review(
            analyzer_markets=analyzer.markets,
            evidence_rows=evidence_rows,
            analyzer_summary={**analyzer.summary, "target_review_input_source": "rebuilt_from_db"},
            evidence_summary={**evidence.summary, "target_review_input_source": "rebuilt_from_db"},
            config=config,
            generated_at=self.now_fn(),
            persist_exports=persist_exports,
        )


def build_target_review(
    *,
    analyzer_markets: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    analyzer_summary: dict[str, Any],
    evidence_summary: dict[str, Any],
    config: PaperMarketMakingTargetReviewConfig,
    generated_at: datetime,
    persist_exports: bool = True,
) -> PaperMarketMakingTargetReviewResult:
    analyzer_by_key = {
        (str(row.get("market_ticker")), str(row.get("best_side"))): row
        for row in analyzer_markets
        if _num(row.get("candidate_quotes")) is not None and int(_num(row.get("candidate_quotes")) or 0) > 0
    }
    evidence_by_key = {
        (str(row.get("market_ticker")), str(row.get("side"))): row
        for row in evidence_rows
        if row.get("market_ticker") and row.get("side")
    }
    keys = sorted(set(analyzer_by_key) | set(evidence_by_key))
    rows = [
        _review_row(key, analyzer_by_key.get(key), evidence_by_key.get(key), config)
        for key in keys
    ]
    rows.sort(key=_review_sort_key)
    summary = _summary(rows, analyzer_markets, evidence_rows, analyzer_summary, evidence_summary, config, generated_at)
    exports = _export(summary, rows) if persist_exports else None
    return PaperMarketMakingTargetReviewResult(summary=summary, rows=rows, exports=exports)


def _review_row(
    key: tuple[str, str],
    analyzer: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    config: PaperMarketMakingTargetReviewConfig,
) -> dict[str, Any]:
    ticker, side = key
    warning_flags = _flags(evidence.get("warning_flags") if evidence else None)
    paper_fills = int(_num(evidence.get("quotes_filled")) or 0) if evidence else 0
    paper_net30 = _num(evidence.get("avg_net_markout_30m_cents")) if evidence else None
    paper_adverse = _num(evidence.get("adverse_selection_rate_30m")) if evidence else None
    analyzer_readiness = str(analyzer.get("readiness") or "") if analyzer else None
    source = _source(analyzer, evidence, paper_net30, paper_adverse, warning_flags, config)
    bucket, reasons = _priority_bucket(
        analyzer=analyzer,
        evidence=evidence,
        analyzer_readiness=analyzer_readiness,
        paper_fills=paper_fills,
        paper_net30=paper_net30,
        paper_adverse=paper_adverse,
        warning_flags=warning_flags,
        config=config,
    )
    return {
        "market_ticker": ticker,
        "side": side,
        "source": source,
        "priority_bucket": bucket,
        "review_reasons": ";".join(reasons),
        "caution_flags": ";".join(warning_flags),
        "analyzer_readiness": analyzer_readiness,
        "analyzer_quotes": int(_num(analyzer.get("candidate_quotes")) or 0) if analyzer else 0,
        "analyzer_fills": int(_num(analyzer.get("trade_evidence_fills")) or 0) if analyzer else 0,
        "analyzer_fill_rate": _num(analyzer.get("fill_rate")) if analyzer else None,
        "analyzer_edge30": _num(analyzer.get("avg_future_edge_30m_cents")) if analyzer else None,
        "analyzer_edge_net": _num(analyzer.get("avg_edge_after_penalty_30m_cents")) if analyzer else None,
        "analyzer_adverse30": _num(analyzer.get("adverse_fill_rate_30m")) if analyzer else None,
        "paper_quotes_total": int(_num(evidence.get("quotes_total")) or 0) if evidence else 0,
        "paper_open_quotes": int(_num(evidence.get("open_quotes")) or 0) if evidence else 0,
        "paper_fills": paper_fills,
        "paper_fill_rate": _num(evidence.get("fill_rate")) if evidence else None,
        "paper_net30": paper_net30,
        "paper_future30_n": int(_num(evidence.get("gross_markout_30m_observations")) or 0) if evidence else 0,
        "paper_adverse30": paper_adverse,
        "stale_open_quote": "stale_open_quote" in warning_flags,
        "too_few_fills": "too_few_fills" in warning_flags,
        "current_unrealized_negative": "current_unrealized_negative" in warning_flags,
        "missing_30m_markout": "missing_30m_markout" in warning_flags,
        "disclaimer": DISCLAIMER,
    }


def _priority_bucket(
    *,
    analyzer: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    analyzer_readiness: str | None,
    paper_fills: int,
    paper_net30: float | None,
    paper_adverse: float | None,
    warning_flags: list[str],
    config: PaperMarketMakingTargetReviewConfig,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if evidence is not None and (paper_net30 is not None and paper_net30 < 0):
        reasons.append("paper_net30_negative")
        return "AVOID_FOR_NOW", reasons
    if evidence is not None and (paper_adverse is not None and paper_adverse >= config.adverse_high_threshold):
        reasons.append("paper_adverse_selection_high")
        return "AVOID_FOR_NOW", reasons
    if analyzer is None:
        reasons.append("not_in_current_analyzer_watchlist")
        if warning_flags:
            reasons.append("paper_warning_flags_present")
            return "DOWNGRADE", reasons
        return "NEED_MORE_EVIDENCE", reasons
    if evidence is None:
        reasons.append("analyzer_only_no_paper_evidence")
        return "NEED_MORE_EVIDENCE", reasons
    if analyzer_readiness in {"ADVERSE_SELECTION_OR_NO_EDGE", "TOO_MUCH_ADVERSE_SELECTION", "ZERO_TRADE_PRINT_FILLS"}:
        reasons.append(f"analyzer_readiness_{analyzer_readiness.lower()}")
        return "DOWNGRADE", reasons
    if "missing_30m_markout" in warning_flags:
        reasons.append("paper_missing_30m_markout")
        return "DOWNGRADE", reasons
    if paper_net30 is None:
        reasons.append("paper_net30_missing")
        return "NEED_MORE_EVIDENCE", reasons
    if paper_fills < config.too_few_fills_threshold or "too_few_fills" in warning_flags:
        reasons.append("paper_too_few_fills")
        return "NEED_MORE_EVIDENCE", reasons
    if paper_net30 <= 0:
        reasons.append("paper_net30_not_positive")
        return "DOWNGRADE", reasons
    if analyzer_readiness not in {"PAPER_WATCHLIST", "PROMISING_NEEDS_MORE_FILLS"}:
        reasons.append("analyzer_not_watchlist_or_promising")
        return "NEED_MORE_EVIDENCE", reasons
    caution_flags = {"stale_open_quote", "current_unrealized_negative", "exploratory_target", "missing_depth_data"}
    if paper_adverse is not None and paper_adverse >= config.adverse_caution_threshold:
        reasons.append("paper_adverse_selection_caution")
        return "CONTINUE_SMALL_SIZE", reasons
    if caution_flags.intersection(warning_flags):
        reasons.append("paper_caution_flags_present")
        return "CONTINUE_SMALL_SIZE", reasons
    reasons.append("analyzer_and_paper_evidence_positive")
    return "CONTINUE_PAPER", reasons


def _source(
    analyzer: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    paper_net30: float | None,
    paper_adverse: float | None,
    flags: list[str],
    config: PaperMarketMakingTargetReviewConfig,
) -> str:
    if analyzer is not None and evidence is not None:
        return "both"
    if analyzer is not None:
        return "analyzer_watchlist" if str(analyzer.get("readiness") or "") in {"PAPER_WATCHLIST", "PROMISING_NEEDS_MORE_FILLS"} else "exploratory"
    if paper_net30 is not None and paper_net30 > 0 and (paper_adverse is None or paper_adverse < config.adverse_high_threshold) and not flags:
        return "evidence_good"
    if flags or (paper_net30 is not None and paper_net30 < 0):
        return "evidence_red_flag"
    return "exploratory"


def _summary(
    rows: list[dict[str, Any]],
    analyzer_markets: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    analyzer_summary: dict[str, Any],
    evidence_summary: dict[str, Any],
    config: PaperMarketMakingTargetReviewConfig,
    generated_at: datetime,
) -> dict[str, Any]:
    counts = {bucket: 0 for bucket in REVIEW_BUCKETS}
    for row in rows:
        counts[str(row["priority_bucket"])] = counts.get(str(row["priority_bucket"]), 0) + 1
    continue_rows = [row for row in rows if row["priority_bucket"] == "CONTINUE_PAPER"]
    caution_rows = [row for row in rows if row["priority_bucket"] in {"DOWNGRADE", "AVOID_FOR_NOW"}]
    status = "TARGET_REVIEW_RESEARCH_ONLY"
    message = "Paper target review generated. Continue candidates are paper-only and do not change trading readiness."
    return {
        "status": status,
        "message": message,
        "generated_at": generated_at.isoformat(),
        "last_days": config.last_days,
        "weather_only": bool(config.weather_only),
        "rows": len(rows),
        "analyzer_rows": len(analyzer_markets),
        "evidence_rows": len(evidence_rows),
        "joined_rows": sum(1 for row in rows if row["source"] == "both"),
        "priority_counts": counts,
        "analyzer_summary": analyzer_summary,
        "evidence_summary": evidence_summary,
        "top_continue_paper": continue_rows[:10],
        "top_downgrade_or_avoid": caution_rows[:10],
        "disclaimer": DISCLAIMER,
    }


def _export(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    csv_path = reports / "paper_market_making_target_review.csv"
    json_path = reports / "paper_market_making_target_review.json"
    md_path = reports / "paper_market_making_target_review.md"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path.write_text(json.dumps({"summary": summary, "rows": rows, "disclaimer": DISCLAIMER}, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(summary, rows), encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "markdown": str(md_path)}


def _load_cached_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]] | None:
    reports = PROJECT_ROOT / "reports"
    market_csv = reports / "market_making_candidates.csv"
    market_summary = reports / "market_making_summary.json"
    evidence_json = reports / "paper_market_making_evidence.json"
    if not market_csv.exists() or not evidence_json.exists():
        return None
    analyzer_markets = pd.read_csv(market_csv).to_dict(orient="records")
    analyzer_summary = {}
    if market_summary.exists():
        try:
            analyzer_summary = json.loads(market_summary.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            analyzer_summary = {}
    try:
        evidence_payload = json.loads(evidence_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    evidence_rows = evidence_payload.get("rows")
    if not isinstance(evidence_rows, list):
        return None
    evidence_summary = evidence_payload.get("summary")
    if not isinstance(evidence_summary, dict):
        evidence_summary = {}
    return analyzer_markets, evidence_rows, analyzer_summary, evidence_summary


def _weather_tickers(storage: Storage) -> set[str]:
    frame = storage.fetch_sql(
        """
        SELECT DISTINCT market_ticker
        FROM parsed_contracts
        WHERE market_ticker IS NOT NULL
        """
    )
    if frame.empty:
        return set()
    return {str(value) for value in frame["market_ticker"].dropna().tolist()}


def _markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    counts = summary.get("priority_counts", {})
    lines = [
        "# Paper Market-Making Target Review",
        "",
        DISCLAIMER,
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Window: last {summary.get('last_days')} days",
        f"- Rows: {summary.get('rows')} total, {summary.get('joined_rows')} with both analyzer and paper evidence",
        f"- Buckets: " + ", ".join(f"{bucket}={counts.get(bucket, 0)}" for bucket in REVIEW_BUCKETS),
        "",
        "## Continue Paper",
        "",
        "| Market | Side | Analyzer Fills | Paper Fills | Paper Net 30m | Paper Adverse 30m | Reasons |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        if row["priority_bucket"] == "CONTINUE_PAPER":
            lines.append(
                f"| {row['market_ticker']} | {row['side']} | {row['analyzer_fills']} | {row['paper_fills']} | "
                f"{_fmt(row['paper_net30'])} | {_fmt(row['paper_adverse30'])} | {row['review_reasons']} |"
            )
    lines.extend(
        [
            "",
            "## All Targets",
            "",
            "| Bucket | Market | Side | Source | Analyzer Fills | Paper Fills | Paper Net 30m | Flags | Reasons |",
            "|---|---|---:|---|---:|---:|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['priority_bucket']} | {row['market_ticker']} | {row['side']} | {row['source']} | "
            f"{row['analyzer_fills']} | {row['paper_fills']} | {_fmt(row['paper_net30'])} | "
            f"{row['caution_flags']} | {row['review_reasons']} |"
        )
    return "\n".join(lines) + "\n"


def _review_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float]:
    bucket_rank = {
        "CONTINUE_PAPER": 0,
        "CONTINUE_SMALL_SIZE": 1,
        "NEED_MORE_EVIDENCE": 2,
        "DOWNGRADE": 3,
        "AVOID_FOR_NOW": 4,
    }.get(str(row.get("priority_bucket")), 9)
    net = _num(row.get("paper_net30"))
    return (bucket_rank, -(net if net is not None else -999.0), -int(row.get("paper_fills") or 0), -float(row.get("analyzer_fills") or 0))


def _flags(value: Any) -> list[str]:
    if not value:
        return []
    return [flag for flag in str(value).split(";") if flag]


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
    return "none" if number is None else f"{number:.3f}"
