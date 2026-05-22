from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import date
from datetime import datetime
from datetime import timezone

from config import settings
from data.storage import Storage
from research.daily_weather_evidence import DailyWeatherEvidenceConfig, DailyWeatherEvidenceReporter


@dataclass
class FakeCoverageResult:
    summary: dict
    days: list[dict]


@dataclass
class FakeReplayResult:
    markets: int
    snapshots: int
    skipped_markets: int
    warnings: list[str]


@dataclass
class FakeMiningResult:
    summary: dict
    signals: list[dict]


@dataclass
class FakeMarketMakingResult:
    summary: dict
    markets: list[dict]
    quote_samples: list[dict]


@dataclass
class FakeReadinessResult:
    status: str = "NOT_READY_NO_EDGE"
    message: str = "Do not trade."
    reasons: list[str] | None = None
    metrics: dict | None = None
    next_command: str = "python main.py analyze-market-making --last-days 7"


class FakeCoverageReporter:
    def __init__(self, *, high_confidence: int = 3):
        self.high_confidence = high_confidence
        self.calls = []

    def build(self, config, *, persist_exports: bool):
        self.calls.append((config, persist_exports))
        return FakeCoverageResult(
            summary={
                "status": "WEATHER_REPLAY_COVERAGE_OK" if self.high_confidence else "WEATHER_REPLAY_COVERAGE_TICKERS_OK_LABELS_MISSING",
                "suggested_replay_command": "python main.py build-recorded-replay --start 2026-05-21 --end 2026-05-21 --recorded-weather-only",
            },
            days=[
                {
                    "day": "2026-05-21",
                    "recorded_orderbook_tickers": 4,
                    "overlap_tickers": 3,
                    "settlement_label_tickers": self.high_confidence,
                    "high_confidence_settlement_label_tickers": self.high_confidence,
                    "missing_settlement_label_tickers": max(3 - self.high_confidence, 0),
                    "likely_replay_markets_gt0": self.high_confidence > 0,
                }
            ],
        )


class FakeReplayBuilder:
    def __init__(self, *, snapshots: int = 100):
        self.snapshots = snapshots
        self.calls = []

    def build(self, **kwargs):
        self.calls.append(kwargs)
        return FakeReplayResult(markets=2 if self.snapshots else 0, snapshots=self.snapshots, skipped_markets=0, warnings=[])


class FakeMiner:
    def __init__(self, *, settled_signals: int = 10, net_pnl_cents: float = 25.0, stress_verdict: str = "passes basic stress"):
        self.settled_signals = settled_signals
        self.net_pnl_cents = net_pnl_cents
        self.stress_verdict = stress_verdict
        self.calls = []

    def mine(self, **kwargs):
        self.calls.append(kwargs)
        return FakeMiningResult(
            summary={
                "verdict": "TEST_MINER_VERDICT",
                "message": "test",
                "rows_scanned": 100,
                "markets_scanned": 2,
                "eligible_rows": 50,
                "signals": self.settled_signals,
                "settled_signals": self.settled_signals,
                "gross_pnl_cents": self.net_pnl_cents + 5.0,
                "fees_cents": 5.0,
                "net_pnl_cents": self.net_pnl_cents,
                "win_rate": 0.6,
                "stress": {"verdict": self.stress_verdict},
            },
            signals=[],
        )


class FakeMarketMakingAnalyzer:
    def analyze(self, **kwargs):
        return FakeMarketMakingResult(
            summary={
                "market_making_verdict": "RESEARCH_READY_NO_PAPER_EDGE_YET",
                "message": "weather only",
                "weather_only": True,
                "snapshots": 100,
                "markets_analyzed": 2,
                "candidate_markets": 1,
                "trade_evidence_fills": 4,
                "trade_evidence_fill_rate": 0.1,
                "avg_future_edge_30m_cents": 1.0,
                "adverse_fill_rate_30m": 0.25,
                "paper_watchlist_candidates": 0,
            },
            markets=[],
            quote_samples=[],
        )


class FakeReadiness:
    def __init__(self):
        self.calls = []

    def evaluate(self, *, last_days: int):
        self.calls.append(last_days)
        return FakeReadinessResult(reasons=["No clean strategy has survived replay."], metrics={})


def _reporter(**overrides):
    return DailyWeatherEvidenceReporter(
        coverage_reporter=overrides.get("coverage_reporter", FakeCoverageReporter()),
        replay_builder=overrides.get("replay_builder", FakeReplayBuilder()),
        miner=overrides.get("miner", FakeMiner()),
        market_making_analyzer=overrides.get("market_making_analyzer", FakeMarketMakingAnalyzer()),
        trading_readiness=overrides.get("trading_readiness", FakeReadiness()),
    )


def test_daily_weather_evidence_positive_research_report_with_labels_replay_and_miner_result():
    result = _reporter(miner=FakeMiner(settled_signals=12, net_pnl_cents=20.0)).build(
        DailyWeatherEvidenceConfig(day=date(2026, 5, 21), max_markets=25),
        persist_exports=False,
    )

    assert result.summary["status"] == "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_SMALL_SAMPLE"
    assert result.summary["research_only"] is True
    assert result.summary["coverage"]["high_confidence_settlement_label_tickers"] == 3
    assert result.summary["recorded_replay"]["snapshots"] == 100
    assert result.summary["miner"]["net_pnl_cents"] == 20.0
    assert result.summary["weather_market_making"]["weather_only"] is True
    assert result.summary["trading_readiness"]["status"] == "NOT_READY_NO_EDGE"


def test_daily_weather_evidence_no_labels_is_research_only_no_replay_ready():
    result = _reporter(coverage_reporter=FakeCoverageReporter(high_confidence=0)).build(
        DailyWeatherEvidenceConfig(day=date(2026, 5, 21)),
        persist_exports=False,
    )

    assert result.summary["status"] == "DAILY_WEATHER_EVIDENCE_NO_REPLAY_READY_LABELS"
    assert result.summary["coverage"]["high_confidence_settlement_label_tickers"] == 0


def test_daily_weather_evidence_zero_snapshots_does_not_crash():
    result = _reporter(replay_builder=FakeReplayBuilder(snapshots=0)).build(
        DailyWeatherEvidenceConfig(day=date(2026, 5, 21)),
        persist_exports=False,
    )

    assert result.summary["status"] == "DAILY_WEATHER_EVIDENCE_NO_REPLAY_SNAPSHOTS"
    assert result.summary["recorded_replay"]["snapshots"] == 0


def test_daily_weather_evidence_negative_net_pnl_is_failed_no_edge():
    result = _reporter(miner=FakeMiner(settled_signals=12, net_pnl_cents=-67.0, stress_verdict="fails 2x fees")).build(
        DailyWeatherEvidenceConfig(day=date(2026, 5, 21)),
        persist_exports=False,
    )

    assert result.summary["status"] == "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_NO_EDGE"
    assert "no edge" in result.summary["message"].lower()
    assert result.summary["miner"]["stress_verdict"] == "fails 2x fees"


def test_daily_weather_evidence_calls_existing_components_with_safe_research_options():
    coverage = FakeCoverageReporter()
    replay = FakeReplayBuilder()
    miner = FakeMiner()
    readiness = FakeReadiness()
    _reporter(
        coverage_reporter=coverage,
        replay_builder=replay,
        miner=miner,
        trading_readiness=readiness,
    ).build(DailyWeatherEvidenceConfig(day=date(2026, 5, 21), max_markets=7), persist_exports=False)

    assert coverage.calls[0][0].last_days == 1
    assert coverage.calls[0][1] is False
    assert replay.calls[0]["start"] == date(2026, 5, 21)
    assert replay.calls[0]["end"] == date(2026, 5, 21)
    assert replay.calls[0]["max_markets"] == 7
    assert replay.calls[0]["allow_unsettled"] is False
    assert replay.calls[0]["historical_weather_fallback"] is False
    assert miner.calls[0]["persist_exports"] is False
    assert readiness.calls == [7]


def test_daily_weather_evidence_skips_replay_build_when_rows_already_exist(tmp_path):
    storage = Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    storage.init_db()
    storage.upsert_recorded_orderbook_replay_snapshot(
        {
            "market_ticker": "KXHIGHNY-26MAY21-B75.5",
            "event_ticker": "E",
            "ts": datetime(2026, 5, 21, 18, tzinfo=timezone.utc),
            "city": "New York",
            "station_code": "KNYC",
            "local_date": "2026-05-21",
            "variable_type": "high_temp",
            "contract_type": "range_bucket",
        }
    )

    result = DailyWeatherEvidenceReporter(
        coverage_reporter=FakeCoverageReporter(),
        miner=FakeMiner(),
        market_making_analyzer=FakeMarketMakingAnalyzer(),
        trading_readiness=FakeReadiness(),
        storage=storage,
    ).build(DailyWeatherEvidenceConfig(day=date(2026, 5, 21)), persist_exports=False)

    assert result.summary["recorded_replay"]["build_skipped"] is True
    assert result.summary["recorded_replay"]["snapshots"] == 1
    assert result.summary["recorded_replay"]["markets"] == 1
    assert "replay_build_skipped_existing_snapshots_present" in result.summary["recorded_replay"]["warnings"]
