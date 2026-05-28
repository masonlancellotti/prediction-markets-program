from __future__ import annotations

import json
from datetime import datetime, timezone

import scan
from relative_value.settlement_evidence_burden import build_settlement_evidence_burden_report
from relative_value.standardized_family_candidates import (
    BTC_BASIS_RISK_REVIEW,
    CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH,
    CRYPTO_RELATED_FV_WATCH,
    DISCOVERY_ONLY,
    FAIR_VALUE_WATCH,
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_FED_FOMC,
    MANUAL_BASIS_RISK_REVIEW,
    NEEDS_ORDERBOOK_ENRICHMENT,
    NEEDS_SOURCE_REGISTRY,
    build_standardized_family_candidates_report,
)


def _write_snapshot(path, *, source: str = "custom", markets: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": source,
                "captured_at": "2026-05-25T12:00:00+00:00",
                "normalized_markets": markets,
            }
        ),
        encoding="utf-8",
    )


def _write_manual_polymarket_crypto_fixture(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fixture_kind": "manual_polymarket_crypto_event_page_snapshot",
                "venue": "polymarket",
                "source_url": "https://polymarket.com/event/what-price-will-bitcoin-hit-in-may-2026",
                "event_slug": "what-price-will-bitcoin-hit-in-may-2026",
                "event_title": "What price will Bitcoin hit in May?",
                "asset": "BTC",
                "measurement_month": "2026-05",
                "measurement_window_start": "2026-05-01T00:00:00-04:00",
                "measurement_window_end": "2026-05-31T23:59:00-04:00",
                "settlement_source": "Binance BTC/USDT 1-minute candles",
                "settlement_source_url": "https://www.binance.com/en/trade/BTC_USDT",
                "rules_text": (
                    "Manual fixture rules. Above selections use any Binance BTC/USDT 1-minute candle final High "
                    "during May 2026; below selections use any Binance BTC/USDT 1-minute candle final Low during May 2026."
                ),
                "markets": [
                    {"direction": "above", "operator": ">=", "threshold": 150000, "label": "up 150,000"},
                    {"direction": "below", "operator": "<=", "threshold": 70000, "label": "down 70,000"},
                ],
                "diagnostic_only": True,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
            }
        ),
        encoding="utf-8",
    )


def _write_manual_polymarket_crypto_single_fixture(
    path,
    *,
    direction: str,
    operator: str,
    threshold: float,
    source: str = "Binance BTC/USDT 1-minute candles",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fixture_kind": "manual_polymarket_crypto_event_page_snapshot",
                "venue": "polymarket",
                "event_slug": "what-price-will-bitcoin-hit-in-may-2026",
                "event_title": "What price will Bitcoin hit in May?",
                "asset": "BTC",
                "measurement_month": "2026-05",
                "measurement_window_start": "2026-05-01T00:00:00-04:00",
                "measurement_window_end": "2026-05-31T23:59:00-04:00",
                "settlement_source": source,
                "settlement_source_url": "https://www.binance.com/en/trade/BTC_USDT",
                "rules_text": (
                    "Manual fixture rules. Above selections use any Binance BTC/USDT 1-minute candle final High "
                    "during May 2026; below selections use any Binance BTC/USDT 1-minute candle final Low during May 2026."
                ),
                "markets": [
                    {
                        "direction": direction,
                        "operator": operator,
                        "threshold": threshold,
                        "label": f"{direction} {threshold}",
                    }
                ],
                "diagnostic_only": True,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
            }
        ),
        encoding="utf-8",
    )


def _write_manual_polymarket_crypto_deadline_range_hit_fixture(
    path,
    *,
    direction: str,
    operator: str,
    threshold: float,
    source: str = "Binance BTC/USDT 1-minute candles",
    measurement_date: str = "December 31, 2026",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    settlement_window = f"deadline_or_date_range_{direction}_hit"
    path.write_text(
        json.dumps(
            {
                "fixture_kind": "manual_polymarket_crypto_event_page_snapshot",
                "venue": "polymarket",
                "event_slug": "when-will-bitcoin-hit-100k",
                "event_title": "When will Bitcoin hit $100k?",
                "asset": "BTC",
                "measurement_date": measurement_date,
                "settlement_shape": "DEADLINE_OR_DATE_RANGE_HIT",
                "settlement_window": settlement_window,
                "settlement_source": source,
                "settlement_source_url": "https://www.binance.com/en/trade/BTC_USDT",
                "rules_text": (
                    "Will resolve YES if any Binance BTC/USDT 1-minute candle reaches the threshold "
                    "by 11:59PM ET on the date specified in the title. Otherwise resolves NO."
                ),
                "markets": [
                    {
                        "direction": direction,
                        "operator": operator,
                        "threshold": threshold,
                        "settlement_shape": "DEADLINE_OR_DATE_RANGE_HIT",
                        "settlement_window": settlement_window,
                        "label": f"{direction} {threshold}",
                    }
                ],
                "diagnostic_only": True,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
            }
        ),
        encoding="utf-8",
    )


def _write_burden(tmp_path):
    burden = build_settlement_evidence_burden_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    burden_path = tmp_path / "reports" / "settlement_evidence_burden.json"
    burden_path.write_text(json.dumps(burden, indent=2, sort_keys=True), encoding="utf-8")
    return burden_path


def _candidate_report(tmp_path):
    return build_standardized_family_candidates_report(
        input_dir=tmp_path / "reports",
        burden_report=_write_burden(tmp_path),
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _fed_market(ticker: str, *, operator_text: str = "greater than", date: str = "Apr 28, 2027", threshold: str = "4.25") -> dict:
    return {
        "venue": "kalshi",
        "event_ticker": "KXFED-27APR",
        "ticker": ticker,
        "title": "Fed title is not used as the typed key.",
        "outcomes": [{"name": "Yes"}, {"name": "No"}],
        "settlement": {
            "settlement_rules_text": (
                f"If the upper bound of the target federal funds rate published on the Federal Reserve's "
                f"official website is {operator_text} {threshold}% following the Federal Reserve's {date} meeting, "
                "then the market resolves to Yes."
            ),
            "settlement_source_url": None,
            "settlement_source_kind": "rules_text_only",
        },
    }


def _btc_market(
    venue: str,
    ticker: str,
    *,
    with_source_index: bool = True,
    source_text: str | None = None,
    time_text: str = "5 PM EDT",
    date_text: str = "May 25, 2026",
    threshold_text: str = "86249.99",
    operator_text: str = "above",
    window_text: str = "simple average of",
    event_slug: str | None = None,
) -> dict:
    source = source_text or ("CF Benchmarks' Bitcoin Real-Time Index (BRTI)" if with_source_index else "the Bitcoin price")
    return {
        "venue": venue,
        "event_ticker": "KXBTC-26MAY2517" if venue == "kalshi" else None,
        "event_slug": event_slug if event_slug is not None else ("btc-may-25-2026" if venue == "polymarket" else None),
        "ticker": ticker,
        "title": "BTC threshold title alone is not enough.",
        "outcomes": [{"name": "Yes"}, {"name": "No"}],
        "settlement": {
            "settlement_rules_text": (
                f"If the {window_text} {source} is {operator_text} {threshold_text} at {time_text} on {date_text}, "
                "then the market resolves to Yes."
            ),
            "settlement_source_url": None,
            "settlement_source_kind": "rules_text_only",
        },
    }


def test_exact_fed_typed_keys_group_correctly(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "fed.json",
        markets=[
            _fed_market("KXFED-27APR-T4.25"),
            _fed_market("KXFED-27APR-T4.25"),
        ],
    )

    report = _candidate_report(tmp_path)

    rows = [row for row in report["rows"] if row["family"] == FAMILY_FED_FOMC]
    assert len(rows) == 1
    assert rows[0]["market_count"] == 2
    assert rows[0]["typed_key"]["meeting_date"] == "Apr 28, 2027"
    assert rows[0]["typed_key"]["rate_bound"] == "upper_bound"
    assert rows[0]["typed_key"]["threshold_percent"] == 4.25
    assert rows[0]["typed_key"]["comparison_operator"] == ">"
    assert rows[0]["allowed_next_action"] == NEEDS_SOURCE_REGISTRY


def test_ibkr_kalshi_route_is_not_independent_cross_venue_pair(tmp_path) -> None:
    routed = _fed_market("KXFED-27APR-T4.25")
    routed.update(
        {
            "venue": "IBKR_KALSHI",
            "source_platform": "IBKR",
            "access_platform": "IBKR",
            "exchange_venue": "KALSHI",
            "executable_venue": "KALSHI",
            "market_id": "ibkr-kalshi-fed",
        }
    )
    _write_snapshot(
        tmp_path / "reports" / "fed_ibkr_kalshi.json",
        markets=[
            _fed_market("KXFED-27APR-T4.25"),
            routed,
        ],
    )

    report = _candidate_report(tmp_path)

    row = next(row for row in report["rows"] if row["family"] == FAMILY_FED_FOMC)
    assert row["venues_involved"] == ["IBKR_KALSHI", "kalshi"]
    assert row["executable_venues_involved"] == ["KALSHI"]
    assert row["cross_venue"] is False
    assert report["pairs"] == []
    assert "ibkr_kalshi_is_same_exchange_as_direct_kalshi" in row["blockers"]
    assert "broker_route_not_independent_venue" in row["blockers"]
    assert "do_not_cross_compare_as_independent_arb" in row["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_ibkr_forecastex_route_remains_separate_from_kalshi(tmp_path) -> None:
    forecastex = _fed_market("FF-JUN26-4.375")
    forecastex.update(
        {
            "venue": "IBKR_FORECASTEX",
            "source_platform": "IBKR",
            "access_platform": "IBKR",
            "exchange_venue": "FORECASTX",
            "executable_venue": "FORECASTX",
            "market_id": "ibkr-forecastex-fed",
        }
    )
    _write_snapshot(
        tmp_path / "reports" / "fed_ibkr_forecastex.json",
        markets=[
            _fed_market("KXFED-27APR-T4.25"),
            forecastex,
        ],
    )

    report = _candidate_report(tmp_path)

    row = next(row for row in report["rows"] if row["family"] == FAMILY_FED_FOMC)
    assert row["executable_venues_involved"] == ["FORECASTX", "KALSHI"]
    assert row["cross_venue"] is True
    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert pair["right"]["source_platform"] == "IBKR" or pair["left"]["source_platform"] == "IBKR"
    assert "ibkr_kalshi_is_same_exchange_as_direct_kalshi" not in row["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_mismatched_fed_operator_or_date_does_not_group(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "fed_mismatch.json",
        markets=[
            _fed_market("KXFED-27APR-T4.25", operator_text="greater than", date="Apr 28, 2027"),
            _fed_market("KXFED-27APR-T4.25", operator_text="less than", date="Apr 28, 2027"),
            _fed_market("KXFED-27APR-T4.25", operator_text="greater than", date="Jun 16, 2027"),
        ],
    )

    rows = [row for row in _candidate_report(tmp_path)["rows"] if row["family"] == FAMILY_FED_FOMC]

    assert len(rows) == 3
    assert sorted(row["market_count"] for row in rows) == [1, 1, 1]
    assert {row["typed_key"]["comparison_operator"] for row in rows} == {">", "<"}
    assert {row["typed_key"]["meeting_date"] for row in rows} == {"Apr 28, 2027", "Jun 16, 2027"}


def test_exact_btc_typed_keys_group_correctly(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc.json",
        markets=[
            _btc_market("kalshi", "KXBTC-26MAY2517-T86249.99"),
            _btc_market("polymarket", "BTC-26MAY2517-T86249.99"),
        ],
    )

    report = _candidate_report(tmp_path)
    rows = [row for row in report["rows"] if row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD]

    assert len(rows) == 1
    row = rows[0]
    assert row["cross_venue"] is True
    assert row["market_count"] == 2
    assert row["typed_key"]["asset"] == "BTC"
    assert row["typed_key"]["threshold_value"] == 86249.99
    assert row["typed_key"]["threshold_operator"] == ">"
    assert row["typed_key"]["measurement_date"] == "May 25, 2026"
    assert row["typed_key"]["price_source_index"] == "cf benchmarks"
    assert row["typed_key"]["timezone"] == "EDT"
    assert row["typed_key"]["settlement_window"] is None
    assert len(report["pairs"]) == 1
    assert report["basis_risk_rows"] == []
    assert report["summary"]["cross_venue_candidate_counts_by_family"][FAMILY_CRYPTO_PRICE_THRESHOLD] == 1


def test_different_known_btc_source_same_time_threshold_operator_is_basis_risk_review(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc_basis.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T86249.99",
                source_text="CF Benchmarks' Bitcoin Real-Time Index (BRTI)",
                window_text="60 second average of",
            ),
            _btc_market(
                "polymarket",
                "poly-btc-coinbase",
                source_text="Coinbase Bitcoin spot price",
                window_text="60 second average of",
                event_slug="bitcoin-above-86249-99-dollars-on-may-25-2026",
            ),
        ],
    )

    report = _candidate_report(tmp_path)

    assert report["pairs"] == []
    summary = report["summary"]
    assert summary["btc_basis_risk_review_count"] == 1
    assert summary["basis_risk_known_reputable_source_pair_count"] == 1
    assert summary["basis_risk_severity_hint_counts"].get("moderate_known_different_sources_same_window") == 1
    row = report["basis_risk_rows"][0]
    assert row["relationship_class"] == BTC_BASIS_RISK_REVIEW
    assert row["allowed_next_action"] == MANUAL_BASIS_RISK_REVIEW
    assert row["source_a"] != row["source_b"]
    assert row["window_a"] == "60_seconds_preceding"
    assert row["window_b"] == "60_seconds_preceding"
    assert "different_known_reputable_sources" in row["basis_risk_reason"]
    assert row["source_pair_known_reputable"] is True
    assert row["basis_risk_severity_hint"] == "moderate_known_different_sources_same_window"
    assert row["exact_payoff_claimed"] is False
    assert row["affects_evaluator_gates"] is False


def test_different_btc_timestamp_blocks_basis_risk_to_discovery(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc_time_mismatch.json",
        markets=[
            _btc_market("kalshi", "KXBTC-26MAY2517-T86249.99", source_text="CF Benchmarks Bitcoin Real-Time Index"),
            _btc_market(
                "polymarket",
                "poly-btc-coinbase",
                source_text="Coinbase Bitcoin spot price",
                time_text="6 PM EDT",
                event_slug="bitcoin-above-86249-99-dollars-on-may-25-2026",
            ),
        ],
    )

    row = _candidate_report(tmp_path)["basis_risk_rows"][0]

    assert row["relationship_class"] == DISCOVERY_ONLY
    assert row["allowed_next_action"] == DISCOVERY_ONLY
    assert "measurement_time_mismatch" in row["blockers"]


def test_unknown_btc_source_blocks_basis_risk_to_discovery(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc_unknown_source.json",
        markets=[
            _btc_market("kalshi", "KXBTC-26MAY2517-T86249.99", source_text="CF Benchmarks Bitcoin Real-Time Index"),
            _btc_market(
                "polymarket",
                "poly-btc-no-source",
                with_source_index=False,
                event_slug="bitcoin-above-86249-99-dollars-on-may-25-2026",
            ),
        ],
    )

    row = _candidate_report(tmp_path)["basis_risk_rows"][0]

    assert row["relationship_class"] == DISCOVERY_ONLY
    assert row["allowed_next_action"] == DISCOVERY_ONLY
    assert "unknown_btc_source" in row["blockers"]


def test_manual_polymarket_crypto_fixture_feeds_standardized_candidates(tmp_path) -> None:
    _write_manual_polymarket_crypto_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "fixture.json"
    )

    report = _candidate_report(tmp_path)
    rows = [
        row
        for row in report["rows"]
        if row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
        and row["venues_involved"] == ["polymarket"]
    ]

    assert len(rows) == 2
    keyed = {row["typed_key"]["threshold_value"]: row for row in rows}
    assert keyed[150000.0]["typed_key"]["threshold_operator"] == ">="
    assert keyed[150000.0]["typed_key"]["measurement_date"] == "2026-05"
    assert keyed[150000.0]["typed_key"]["price_source_index"] == "Binance BTC/USDT 1-minute candles"
    assert keyed[150000.0]["typed_key"]["settlement_window"] == (
        "any Binance BTC/USDT 1-minute candle final High during month"
    )
    assert keyed[70000.0]["typed_key"]["threshold_operator"] == "<="
    assert keyed[70000.0]["typed_key"]["settlement_window"] == (
        "any Binance BTC/USDT 1-minute candle final Low during month"
    )
    for row in rows:
        assert row["allowed_next_action"] == NEEDS_ORDERBOOK_ENRICHMENT
        assert "monthly_extreme_window_not_point_in_time" in row["blockers"]
        assert "manual_fixture_not_live_market_snapshot" in row["blockers"]
        assert row["exact_payoff_claimed"] is False
        assert row["paper_candidate_emitted"] is False
    assert report["summary"]["paper_candidate_count"] == 0


def test_polymarket_monthly_high_vs_kalshi_point_in_time_produces_crypto_fv_watch(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_100k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T100000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="100000",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_single_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_100k.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["btc_basis_risk_review_count"] == 0
    assert report["summary"]["crypto_related_fv_watch_rows"] == 1
    assert report["summary"]["crypto_related_fv_watch_by_asset"] == {"BTC": 1}
    row = report["crypto_related_fv_watch_rows"][0]
    assert row["relationship_class"] == CRYPTO_RELATED_FV_WATCH
    assert row["allowed_next_action"] == FAIR_VALUE_WATCH
    assert row["not_exact_payoff_reason"] == "monthly_extreme_vs_point_in_time"
    assert row["source_a"] == "cf benchmarks"
    assert row["source_b"] == "Binance BTC/USDT 1-minute candles"
    assert row["window_a"] == "60_seconds_preceding"
    assert row["window_b"] == "any Binance BTC/USDT 1-minute candle final High during month"
    assert row["fair_value_relevance_reason"] == (
        "same_crypto_asset_threshold_direction_with_monthly_extreme_vs_point_in_time_window"
    )
    assert set(row["blockers"]) >= {
        "monthly_extreme_window_not_point_in_time",
        "not_same_payoff",
        "not_evaluator_eligible",
    }
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["exact_payoff_claimed"] is False
    assert row["paper_candidate_emitted"] is False


def test_polymarket_monthly_low_vs_kalshi_point_in_time_produces_crypto_fv_watch(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_70k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T70000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="70000",
                operator_text="at most",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_single_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_70k.json",
        direction="below",
        operator="<=",
        threshold=70000,
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["crypto_related_fv_watch_rows"] == 1
    row = report["crypto_related_fv_watch_rows"][0]
    assert row["relationship_class"] == CRYPTO_RELATED_FV_WATCH
    assert row["typed_key"]["direction"] == "below"
    assert row["window_b"] == "any Binance BTC/USDT 1-minute candle final Low during month"
    assert row["allowed_next_action"] == FAIR_VALUE_WATCH


def test_mismatched_threshold_does_not_produce_crypto_fv_watch(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_100001.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T100001",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="100001",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_single_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_100k.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )

    report = _candidate_report(tmp_path)

    assert report["crypto_related_fv_watch_rows"] == []
    assert report["summary"]["crypto_related_fv_watch_rows"] == 0


def test_unknown_source_blocks_crypto_fv_watch_to_discovery_only(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_100k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T100000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="100000",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_single_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_unknown_source.json",
        direction="above",
        operator=">=",
        threshold=100000,
        source="Unreviewed BTC candles",
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["crypto_related_fv_watch_rows"] == 0
    assert report["summary"]["crypto_related_fv_watch_relationship_class_counts"] == {"DISCOVERY_ONLY": 1}
    row = report["crypto_related_fv_watch_rows"][0]
    assert row["relationship_class"] == DISCOVERY_ONLY
    assert row["allowed_next_action"] == DISCOVERY_ONLY
    assert "unknown_crypto_source" in row["blockers"]
    assert row["affects_evaluator_gates"] is False
    assert row["paper_candidate_emitted"] is False


def test_polymarket_deadline_range_hit_vs_kalshi_point_in_time_produces_deadline_fv_watch(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_100k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T100000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="100000",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_deadline_range_hit_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_deadline_100k.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["crypto_related_fv_watch_rows"] == 0
    assert report["summary"]["crypto_deadline_range_hit_fv_watch_rows"] == 1
    assert report["summary"]["crypto_deadline_range_hit_fv_watch_by_asset"] == {"BTC": 1}
    row = report["crypto_deadline_range_hit_fv_watch_rows"][0]
    assert row["relationship_class"] == CRYPTO_DEADLINE_RANGE_HIT_FV_WATCH
    assert row["allowed_next_action"] == FAIR_VALUE_WATCH
    assert row["not_exact_payoff_reason"] == "deadline_or_date_range_hit_vs_point_in_time"
    assert "dominance_hint" in row
    assert row["dominance_hint"].startswith("deadline_or_range_hit_probability_geq_point_in_time")
    # One leg must be a point-in-time-equivalent window, the other a deadline/range-hit.
    windows = {row["window_a"], row["window_b"]}
    assert any(w in {"point_in_time", "60_seconds_preceding", "instant_tick"} for w in windows)
    assert any(w.startswith("deadline_or_date_range_") for w in windows)
    assert set(row["blockers"]) >= {
        "deadline_or_date_range_hit_window_not_point_in_time",
        "not_same_payoff",
        "not_evaluator_eligible",
        "one_sided_dominance_only_deadline_hit_geq_point_in_time",
    }
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["exact_payoff_claimed"] is False
    assert row["paper_candidate_emitted"] is False


def test_deadline_range_hit_unknown_source_blocks_to_discovery_only(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_100k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T100000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="100000",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_deadline_range_hit_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_unreviewed.json",
        direction="above",
        operator=">=",
        threshold=100000,
        source="Unreviewed BTC candles",
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["crypto_deadline_range_hit_fv_watch_rows"] == 0
    assert (
        report["summary"]["crypto_deadline_range_hit_fv_watch_relationship_class_counts"]
        == {"DISCOVERY_ONLY": 1}
    )
    row = report["crypto_deadline_range_hit_fv_watch_rows"][0]
    assert row["relationship_class"] == DISCOVERY_ONLY
    assert row["allowed_next_action"] == DISCOVERY_ONLY
    assert "unknown_crypto_source" in row["blockers"]
    assert row["affects_evaluator_gates"] is False
    assert row["paper_candidate_emitted"] is False


def test_deadline_range_hit_threshold_mismatch_does_not_pair(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_99k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T99000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="99000",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_deadline_range_hit_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_100k_deadline.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )

    report = _candidate_report(tmp_path)

    assert report["crypto_deadline_range_hit_fv_watch_rows"] == []
    assert report["summary"]["crypto_deadline_range_hit_fv_watch_rows"] == 0


def test_deadline_range_hit_does_not_pair_with_monthly_extreme(tmp_path) -> None:
    _write_manual_polymarket_crypto_single_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_monthly.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )
    # Same Polymarket venue twice — venue collision should already prevent pairing,
    # but also verify deadline class does not engage when neither leg is point_in_time.
    _write_manual_polymarket_crypto_deadline_range_hit_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_deadline.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["crypto_deadline_range_hit_fv_watch_rows"] == 0
    assert report["summary"]["crypto_related_fv_watch_rows"] == 0


def test_crypto_fv_watch_report_does_not_emit_paper_or_exact_evaluator_language(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_btc_100k.json",
        markets=[
            _btc_market(
                "kalshi",
                "KXBTC-26MAY2517-T100000",
                source_text="CF Benchmarks Bitcoin Real-Time Index",
                threshold_text="100000",
                operator_text="at least",
                window_text="60 second average of",
            )
        ],
    )
    _write_manual_polymarket_crypto_single_fixture(
        tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "poly_btc_100k.json",
        direction="above",
        operator=">=",
        threshold=100000,
    )

    encoded = json.dumps(_candidate_report(tmp_path))

    assert "PAPER_CANDIDATE" not in encoded
    assert "EXACT_PAYOFF_REVIEW_READY" not in encoded
    assert "EXECUTION_EVALUATION_READY" not in encoded


def test_missing_btc_source_index_blocks_candidate_from_review(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc_missing_index.json",
        markets=[
            _btc_market("kalshi", "KXBTC-26MAY2517-T86249.99", with_source_index=False),
            _btc_market(
                "polymarket",
                "poly-btc-no-source",
                with_source_index=False,
                event_slug="bitcoin-above-86249-99-dollars-on-may-25-2026",
            ),
        ],
    )

    rows = [row for row in _candidate_report(tmp_path)["rows"] if row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD]

    assert len(rows) == 1
    assert rows[0]["allowed_next_action"] == DISCOVERY_ONLY
    assert "price_source_index" in rows[0]["missing_typed_keys"]
    assert "missing_typed_key:price_source_index" in rows[0]["blockers"]


def test_title_only_similarity_does_not_group(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "title_only.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "random-event",
                "ticker": "TITLE-ONLY-1",
                "title": "Fed Apr 28 2027 above 4.25 and BTC above 86249.99 CF Benchmarks",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    )

    report = _candidate_report(tmp_path)

    assert report["rows"] == []
    assert report["pairs"] == []


def test_graph_or_llm_hints_cannot_fill_typed_keys(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "hints.json",
        markets=[
            {
                **_btc_market("kalshi", "KXBTC-26MAY2517-T86249.99", with_source_index=False),
                "graph_hints": {"typed_keys": {"price_source_index": "cf benchmarks"}},
                "llm_review": {"typed_keys": {"price_source_index": "cf benchmarks"}},
            }
        ],
    )

    rows = [row for row in _candidate_report(tmp_path)["rows"] if row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD]

    assert len(rows) == 1
    assert rows[0]["typed_key"]["price_source_index"] is None
    assert "price_source_index" in rows[0]["missing_typed_keys"]
    assert rows[0]["allowed_next_action"] == DISCOVERY_ONLY


def test_output_remains_diagnostic_only(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "safe.json",
        markets=[_fed_market("KXFED-27APR-T4.25")],
    )

    report = _candidate_report(tmp_path)

    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["affects_evaluator_gates"] is False
    assert report["safety"]["same_payoff_equivalence_claimed"] is False
    assert report["safety"]["family_typed_review_can_promote_to_exact_or_execution"] is False
    assert all(row["diagnostic_only"] is True for row in report["rows"])
    assert all(row["affects_evaluator_gates"] is False for row in report["rows"])
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_generate_standardized_family_candidates_cli_writes_outputs(tmp_path, capsys) -> None:
    _write_snapshot(
        tmp_path / "reports" / "cli.json",
        markets=[_fed_market("KXFED-27APR-T4.25")],
    )
    burden_path = _write_burden(tmp_path)
    json_output = tmp_path / "candidates.json"
    csv_output = tmp_path / "candidates.csv"
    markdown_output = tmp_path / "candidates.md"

    result = scan.main(
        [
            "generate-standardized-family-candidates",
            "--input-dir",
            str(tmp_path / "reports"),
            "--burden-report",
            str(burden_path),
            "--json-output",
            str(json_output),
            "--csv-output",
            str(csv_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "standardized_family_candidates_status=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "standardized_family_candidates_v1"
    assert payload["summary"]["candidate_group_count"] == 1
    assert csv_output.exists()
    assert markdown_output.exists()
