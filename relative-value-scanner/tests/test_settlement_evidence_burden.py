from __future__ import annotations

import json
from datetime import datetime, timezone

import scan
from relative_value.settlement_evidence_burden import (
    BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED,
    BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED,
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_FED_FOMC,
    FAMILY_POLITICS_NEWS,
    FAMILY_SPORTS_FUTURES_CHAMPIONSHIP,
    FAMILY_WEATHER,
    TIER_BLOCKED,
    TIER_DISCOVERY_READY,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_EXECUTION_EVALUATION_READY,
    TIER_FAMILY_TYPED_REVIEW_READY,
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
    build_settlement_evidence_burden_report,
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


def _manual_polymarket_crypto_fixture() -> dict:
    return {
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
            "This manual saved fixture resolves using Binance BTC/USDT 1-minute candles during May 2026. "
            "Above markets use any final High during the month; below markets use any final Low during the month."
        ),
        "markets": [
            {"direction": "above", "operator": ">=", "threshold": 150000, "label": "up 150,000"},
            {"direction": "below", "operator": "<=", "threshold": 70000, "label": "down 70,000"},
        ],
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
    }


def _report(tmp_path, *, registry_path=None):
    return build_settlement_evidence_burden_report(
        input_dir=tmp_path / "reports",
        registry_path=registry_path,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _find(report, ticker: str):
    return next(row for row in report["markets"] if row["ticker"] == ticker)


def test_weather_without_station_or_source_remains_strict_blocked(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "weather.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXWEATHER-26MAY",
                "ticker": "KXWEATHER-26MAY-T75",
                "title": "Will it be hot in New York on May 25, 2026?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": "Resolves if the high in NYC is at least 75 degrees.",
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXWEATHER-26MAY-T75")
    assert row["family"] == FAMILY_WEATHER
    assert row["evidence_burden"] == BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED
    assert row["review_readiness_tier"] == TIER_DISCOVERY_READY
    assert row["source_url_required_for_review"] is True
    assert "high_ambiguity_requires_explicit_source" in row["blockers"]


def test_weather_with_title_only_station_guess_does_not_pass(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "weather_title.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXWEATHER-26MAY",
                "ticker": "KXWEATHER-26MAY-T75",
                "title": "Will JFK airport be above 90 degrees on May 25, 2026?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": "Resolves Yes if it is hot at the airport.",
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXWEATHER-26MAY-T75")
    assert row["family"] == FAMILY_WEATHER
    assert row["review_readiness_tier"] == TIER_DISCOVERY_READY
    assert "station" not in row["present_typed_keys"]
    assert "observation_source" not in row["present_typed_keys"]


def test_fed_fomc_complete_typed_keys_without_url_becomes_family_typed_review_ready(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "fed.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound of the federal funds rate be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the upper bound of the target federal funds rate published on the Federal Reserve's "
                        "official website is greater than 4.25% following the Federal Reserve's Apr 28, 2027 meeting, "
                        "then the market resolves to Yes."
                    ),
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXFED-27APR-T4.25")
    assert row["family"] == FAMILY_FED_FOMC
    assert row["evidence_burden"] == BURDEN_STANDARDIZED_TYPED_KEYS_REVIEW_ALLOWED
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY
    assert set(row["present_typed_keys"]) >= {"meeting_date", "rate_bound", "threshold_percent", "source_convention"}
    assert row["missing_typed_keys"] == []
    assert row["not_evaluator_reason"] == "missing_settlement_source_for_evaluator"
    assert row["source_url_required_for_exact_evaluator"] is True


def test_btc_threshold_with_complete_typed_keys_becomes_family_typed_review_ready(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXBTC-26MAY2517",
                "ticker": "KXBTC-26MAY2517-T86249.99",
                "title": "Bitcoin price range on May 25, 2026?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the simple average of CF Benchmarks' Bitcoin Real-Time Index (BRTI) is above 86249.99 "
                        "at 5 PM EDT on May 25, 2026, then the market resolves to Yes."
                    ),
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXBTC-26MAY2517-T86249.99")
    assert row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY
    assert set(row["present_typed_keys"]) >= {
        "asset",
        "threshold_value",
        "threshold_operator",
        "measurement_date",
        "price_source_index",
    }


def test_polymarket_btc_slug_with_k_suffix_threshold_parses(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_btc_100k.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "bitcoin-above-100k-on-may-25-2026",
                "ticker": "poly-btc-100k",
                "title": "Title must not be settlement evidence.",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market resolves Yes if the Coinbase BTC spot price is above the listed strike "
                        "at 5 PM EDT on May 25, 2026."
                    ),
                },
            }
        ],
    )

    row = _find(_report(tmp_path), "poly-btc-100k")

    assert row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert row["typed_key_evidence"]["asset"]["value"] == "BTC"
    assert row["typed_key_evidence"]["threshold_value"]["value"] == 100000.0
    assert row["typed_key_evidence"]["threshold_value"]["source"] == "event_slug:crypto_threshold_pattern"
    assert row["typed_key_evidence"]["threshold_operator"]["value"] == "above"
    assert row["typed_key_evidence"]["measurement_date"]["value"] == "May 25, 2026"
    assert row["typed_key_evidence"]["measurement_time"]["value"] == "5 PM EDT"
    assert row["typed_key_evidence"]["price_source_index"]["value"] == "coinbase"
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY


def test_polymarket_crypto_slug_with_decimal_or_dollars_threshold_parses(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_crypto_thresholds.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "bitcoin-above-86249-99-dollars-on-may-25-2026",
                "ticker": "poly-btc-decimal",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market resolves Yes if the Coinbase BTC spot price is above the listed strike "
                        "at 5 PM EDT on May 25, 2026."
                    ),
                },
            },
            {
                "venue": "polymarket",
                "event_slug": "ethereum-below-86249-dollars-on-may-25-2026",
                "ticker": "poly-eth-dollars",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market resolves Yes if the Coinbase ETH spot price is below the listed strike "
                        "at 5 PM EDT on May 25, 2026."
                    ),
                },
            },
        ],
    )

    report = _report(tmp_path)
    btc = _find(report, "poly-btc-decimal")
    eth = _find(report, "poly-eth-dollars")

    assert btc["typed_key_evidence"]["asset"]["value"] == "BTC"
    assert btc["typed_key_evidence"]["threshold_value"]["value"] == 86249.99
    assert eth["typed_key_evidence"]["asset"]["value"] == "ETH"
    assert eth["typed_key_evidence"]["threshold_value"]["value"] == 86249.0


def test_polymarket_crypto_row_without_explicit_source_keeps_price_source_missing(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_btc_missing_source.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "btc-above-120000-on-may-25-2026",
                "ticker": "poly-btc-no-source",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market resolves Yes if BTC is above the listed strike at 5 PM EDT on May 25, 2026."
                    ),
                },
            }
        ],
    )

    row = _find(_report(tmp_path), "poly-btc-no-source")

    assert row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert row["typed_key_evidence"]["threshold_value"]["value"] == 120000.0
    assert "price_source_index" in row["missing_typed_keys"]
    assert row["review_readiness_tier"] == TIER_DISCOVERY_READY
    assert "missing_required_typed_keys" in row["blockers"]


def test_polymarket_reviewed_threshold_slug_pattern_classifies_crypto(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_btc_when_will.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "when-will-bitcoin-hit-150k",
                "ticker": "poly-btc-hit-150k",
                "title": "Title is ignored for typed-key promotion.",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market resolves Yes if the Coinbase BTC spot price is above the listed strike "
                        "at 5 PM EDT on May 25, 2026."
                    ),
                },
            }
        ],
    )

    row = _find(_report(tmp_path), "poly-btc-hit-150k")

    assert row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert "polymarket.event_slug_threshold_pattern" in row["family_signals"]
    assert row["typed_key_evidence"]["asset"]["value"] == "BTC"
    assert row["typed_key_evidence"]["threshold_value"]["value"] == 150000.0
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY


def test_polymarket_compound_crypto_slugs_do_not_match_threshold_allowlist(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_compound_crypto_slugs.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "will-bitcoin-hit-1m-before-gta-vi",
                "ticker": "poly-btc-gta",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            },
            {
                "venue": "polymarket",
                "event_slug": "microstrategy-sell-any-bitcoin-in-2025",
                "ticker": "poly-mstr-sell-btc",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            },
            {
                "venue": "polymarket",
                "event_slug": "will-el-salvador-hold-1b-of-btc-by-2026",
                "ticker": "poly-el-salvador-btc",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            },
        ],
    )

    report = _report(tmp_path)

    for ticker in ("poly-btc-gta", "poly-mstr-sell-btc", "poly-el-salvador-btc"):
        row = _find(report, ticker)
        assert row["family"] != FAMILY_CRYPTO_PRICE_THRESHOLD
        assert "polymarket.event_slug_threshold_pattern" not in row["family_signals"]


def test_polymarket_reviewed_eth_threshold_slug_pattern_classifies_crypto(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_eth_when_will.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "when-will-ethereum-reach-5000",
                "ticker": "poly-eth-reach-5000",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market resolves Yes if the Coinbase ETH spot price is above the listed strike "
                        "at 5 PM EDT on May 25, 2026."
                    ),
                },
            }
        ],
    )

    row = _find(_report(tmp_path), "poly-eth-reach-5000")

    assert row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert row["typed_key_evidence"]["asset"]["value"] == "ETH"
    assert row["typed_key_evidence"]["threshold_value"]["value"] == 5000.0
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY


def test_manual_polymarket_crypto_event_fixture_expands_market_rows(tmp_path) -> None:
    fixture_path = tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "fixture.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(_manual_polymarket_crypto_fixture()), encoding="utf-8-sig")

    report = _report(tmp_path)
    manual_rows = [
        row
        for row in report["markets"]
        if row["event_slug"] == "what-price-will-bitcoin-hit-in-may-2026"
    ]

    assert len(manual_rows) == 2
    assert report["summary"]["warning_count"] == 0
    assert {row["direction"] for row in manual_rows} == {"above", "below"}
    assert {row["family"] for row in manual_rows} == {FAMILY_CRYPTO_PRICE_THRESHOLD}
    assert {row["review_readiness_tier"] for row in manual_rows} == {TIER_SETTLEMENT_SOURCE_REVIEW_READY}
    assert report["summary"]["by_review_readiness_tier"].get(TIER_EXACT_PAYOFF_REVIEW_READY, 0) == 0
    assert report["summary"]["by_review_readiness_tier"].get(TIER_EXECUTION_EVALUATION_READY, 0) == 0
    for row in manual_rows:
        assert row["diagnostic_only"] is True
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["settlement_source_url_present"] is True
        assert row["typed_key_evidence"]["asset"]["value"] == "BTC"
        assert row["typed_key_evidence"]["price_source_index"]["value"] == "Binance BTC/USDT 1-minute candles"
        assert row["typed_key_evidence"]["measurement_date"]["value"] == "2026-05"
        assert "manual_fixture_not_live_market_snapshot" in row["blockers"]
        assert "polymarket_binance_source_not_exact_with_kalshi_brti" in row["blockers"]
        assert "monthly_extreme_window_not_point_in_time" in row["blockers"]
        assert "missing_quote_captured_at" in row["blockers"]


def test_manual_polymarket_crypto_event_fixture_direction_semantics(tmp_path) -> None:
    fixture_path = tmp_path / "reports" / "manual_poly_crypto.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(_manual_polymarket_crypto_fixture()), encoding="utf-8")

    rows = {
        row["direction"]: row
        for row in _report(tmp_path)["markets"]
        if row["event_slug"] == "what-price-will-bitcoin-hit-in-may-2026"
    }

    above = rows["above"]
    below = rows["below"]
    assert above["typed_key_evidence"]["threshold_operator"]["value"] == ">="
    assert above["typed_key_evidence"]["threshold_value"]["value"] == 150000.0
    assert above["settlement_window"] == "any Binance BTC/USDT 1-minute candle final High during month"
    assert above["typed_key_evidence"]["settlement_window"]["value"] == above["settlement_window"]
    assert below["typed_key_evidence"]["threshold_operator"]["value"] == "<="
    assert below["typed_key_evidence"]["threshold_value"]["value"] == 70000.0
    assert below["settlement_window"] == "any Binance BTC/USDT 1-minute candle final Low during month"
    assert below["typed_key_evidence"]["settlement_window"]["value"] == below["settlement_window"]


def test_btc_missing_price_source_index_remains_blocked_from_typed_review(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "btc_missing_source.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXBTC-26MAY2517",
                "ticker": "KXBTC-26MAY2517-T86249.99",
                "title": "BTC threshold on May 25, 2026",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": "If Bitcoin closes above 86249.99 on May 25, 2026, market resolves Yes.",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXBTC-26MAY2517-T86249.99")
    assert row["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert "price_source_index" in row["missing_typed_keys"]
    assert row["review_readiness_tier"] == TIER_DISCOVERY_READY
    assert "missing_required_typed_keys" in row["blockers"]


def test_sports_championship_typed_keys_complete_does_not_force_exact_payoff(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "sports.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXNBA-26",
                "ticker": "KXNBA-26-SAS",
                "title": "Will San Antonio win the 2026 NBA championship?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If San Antonio win the 2026 Pro Basketball Finals, then the market resolves to Yes. "
                        "Source: NBA.com official records."
                    ),
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXNBA-26-SAS")
    assert row["family"] == FAMILY_SPORTS_FUTURES_CHAMPIONSHIP
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY
    # Sports family is eligible for EXACT_PAYOFF only with explicit settlement source.
    assert "championship_or_event_name" in row["present_typed_keys"]
    assert row["not_evaluator_reason"] == "missing_settlement_source_for_evaluator"
    assert row["source_url_required_for_exact_evaluator"] is True


def test_title_similarity_alone_cannot_create_typed_review_readiness(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "title_only.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "what-will-happen-on-may-25",
                "ticker": "title-only-1",
                "title": (
                    "Federal Reserve sets rates Apr 28, 2027; will BTC stay above 86249.99 on May 25, 2026? "
                    "CF Benchmarks BRTI."
                ),
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "title-only-1")
    # No deterministic slug match; rules_text is empty -> family falls to OTHER_UNKNOWN.
    assert row["family"] not in {FAMILY_FED_FOMC, FAMILY_CRYPTO_PRICE_THRESHOLD}
    assert row["review_readiness_tier"] in {TIER_DISCOVERY_READY, TIER_BLOCKED}
    assert "family_not_classified" in row["blockers"] or row["family"] == "OTHER_UNKNOWN"


def test_polymarket_description_only_is_not_settlement_source(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "poly_desc.json",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "nba-coach-of-the-year-2026",
                "ticker": "poly-643794",
                "title": "Will JB Bickerstaff win the 2025-2026 NBA Coach of the Year?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "This market will resolve according to the player who is awarded the 2025-26 NBA Coach of "
                        "the Year. Source: NBA.com awards page."
                    ),
                    "settlement_source_url": None,
                    "settlement_source_kind": "description_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "poly-643794")
    assert row["family"] == FAMILY_SPORTS_FUTURES_CHAMPIONSHIP
    assert row["settlement_source_url_present"] is False
    # description_only_kind does not count as settlement source.
    assert row["review_readiness_tier"] in {TIER_DISCOVERY_READY, TIER_FAMILY_TYPED_REVIEW_READY}
    assert row["source_url_required_for_exact_evaluator"] is True


def test_kalshi_rules_text_supports_review_but_not_source_proof(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_rules.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the upper bound of the target federal funds rate is greater than 4.25% following the "
                        "Federal Reserve's Apr 28, 2027 meeting, market resolves to Yes."
                    ),
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXFED-27APR-T4.25")
    assert row["settlement_source_url_present"] is False
    assert row["settlement_source_kind"] == "rules_text_only"
    assert row["review_readiness_tier"] == TIER_FAMILY_TYPED_REVIEW_READY
    assert row["source_url_required_for_exact_evaluator"] is True


def test_canonical_convention_registry_upgrades_review_readiness_only_with_reviewer_evidence(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "KXFED-fomc-canonical-2026Q2",
                        "family": FAMILY_FED_FOMC,
                        "reviewer": "mason",
                        "reviewed_at": "2026-05-20T00:00:00+00:00",
                        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXFED"},
                        "typed_key_requirements": {
                            "required": [
                                "meeting_date",
                                "rate_bound",
                                "threshold_percent",
                                "source_convention",
                            ],
                            "match": {"source_convention": "federal_reserve_official_website"},
                        },
                        "canonical_source_kind": "official_source_url",
                        "canonical_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                        "evidence_quote_or_excerpt": {
                            "kind": "official_source_excerpt",
                            "text": "Federal Reserve official FOMC meeting calendar and policy decision source.",
                        },
                        "limitations": ["Manual registry convention only."],
                        "review_until": "2026-12-31",
                        "confidence": "high",
                    },
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "incomplete-entry-no-reviewer",
                        "family": FAMILY_FED_FOMC,
                        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXFED"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_snapshot(
        tmp_path / "reports" / "fed.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the upper bound of the target federal funds rate published on the Federal Reserve's "
                        "official website is greater than 4.25% following the Federal Reserve's Apr 28, 2027 meeting, "
                        "then the market resolves to Yes."
                    ),
                    "settlement_source_url": None,
                    "settlement_source_kind": "rules_text_only",
                },
            }
        ],
    )

    report = _report(tmp_path, registry_path=registry_path)
    row = _find(report, "KXFED-27APR-T4.25")
    assert row["registry_match"] is not None
    assert row["registry_match"]["registry_entry_id"] == "KXFED-fomc-canonical-2026Q2"
    assert row["registry_match"]["reviewer"] == "mason"
    assert row["review_readiness_tier"] == TIER_SETTLEMENT_SOURCE_REVIEW_READY
    assert row["quote_freshness_status"]["blocker"] == "missing_quote_captured_at"
    assert "missing_quote_captured_at" in row["blockers"]
    # Even with the registry match, exact/evaluator tiers require fresh saved quote capture evidence.
    assert row["review_readiness_tier"] != TIER_EXACT_PAYOFF_REVIEW_READY
    assert row["review_readiness_tier"] != TIER_EXECUTION_EVALUATION_READY


def test_fresh_normalized_quote_capture_allows_exact_review_but_not_execution_without_depth(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "KXFED-fomc-canonical-2026Q2",
                        "family": FAMILY_FED_FOMC,
                        "reviewer": "mason",
                        "reviewed_at": "2026-05-20T00:00:00+00:00",
                        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXFED"},
                        "typed_key_requirements": {
                            "required": [
                                "meeting_date",
                                "rate_bound",
                                "threshold_percent",
                                "source_convention",
                            ],
                            "match": {"source_convention": "federal_reserve_official_website"},
                        },
                        "canonical_source_kind": "official_source_url",
                        "canonical_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                        "evidence_quote_or_excerpt": "Federal Reserve official FOMC meeting source.",
                        "limitations": ["Fixture registry entry."],
                        "review_until": "2026-12-31",
                        "confidence": "high",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_snapshot(
        tmp_path / "reports" / "fed.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the upper bound of the target federal funds rate published on the Federal Reserve's "
                        "official website is greater than 4.25% following the Federal Reserve's Apr 28, 2027 meeting, "
                        "then the market resolves to Yes."
                    ),
                },
            }
        ],
    )
    (tmp_path / "reports" / "normalized_markets_v0.json").write_text(
        json.dumps(
            {
                "source": "normalized_market_contract_v0",
                "normalized_markets": [
                    {
                        "venue": "kalshi",
                        "event_id": "KXFED-27APR",
                        "event_ticker": "KXFED-27APR",
                        "market_id": "KXFED-27APR-T4.25",
                        "ticker": "KXFED-27APR-T4.25",
                        "readiness": {"quote_depth_ready": False, "fee_metadata_ready": False},
                        "quote_depth": {
                            "captured_at": "2026-01-01T00:00:00+00:00",
                            "blockers": ["missing_depth"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = _report(tmp_path, registry_path=registry_path)
    row = _find(report, "KXFED-27APR-T4.25")

    assert row["quote_freshness_status"]["is_fresh"] is True
    assert row["quote_freshness_status"]["blocker"] is None
    assert row["review_readiness_tier"] == TIER_EXACT_PAYOFF_REVIEW_READY
    assert row["review_readiness_tier"] != TIER_EXECUTION_EVALUATION_READY
    assert "missing_quote_depth_for_execution" in row["blockers"]


def test_stale_normalized_quote_capture_blocks_exact_review(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "KXFED-fomc-canonical-2026Q2",
                        "family": FAMILY_FED_FOMC,
                        "reviewer": "mason",
                        "reviewed_at": "2026-05-20T00:00:00+00:00",
                        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXFED"},
                        "typed_key_requirements": {
                            "required": [
                                "meeting_date",
                                "rate_bound",
                                "threshold_percent",
                                "source_convention",
                            ],
                            "match": {"source_convention": "federal_reserve_official_website"},
                        },
                        "canonical_source_kind": "official_source_url",
                        "canonical_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                        "evidence_quote_or_excerpt": "Federal Reserve official FOMC meeting source.",
                        "limitations": ["Fixture registry entry."],
                        "review_until": "2026-12-31",
                        "confidence": "high",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_snapshot(
        tmp_path / "reports" / "fed.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the upper bound of the target federal funds rate published on the Federal Reserve's "
                        "official website is greater than 4.25% following the Federal Reserve's Apr 28, 2027 meeting, "
                        "then the market resolves to Yes."
                    ),
                },
            }
        ],
    )
    (tmp_path / "reports" / "normalized_markets_v0.json").write_text(
        json.dumps(
            {
                "source": "normalized_market_contract_v0",
                "normalized_markets": [
                    {
                        "venue": "kalshi",
                        "event_ticker": "KXFED-27APR",
                        "market_id": "KXFED-27APR-T4.25",
                        "ticker": "KXFED-27APR-T4.25",
                        "readiness": {"quote_depth_ready": True, "fee_metadata_ready": True},
                        "quote_depth": {"captured_at": "2025-12-31T23:00:00+00:00"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = _report(tmp_path, registry_path=registry_path)
    row = _find(report, "KXFED-27APR-T4.25")

    assert row["quote_freshness_status"]["blocker"] == "stale_quote"
    assert "stale_quote" in row["blockers"]
    assert row["review_readiness_tier"] == TIER_SETTLEMENT_SOURCE_REVIEW_READY


def test_graph_or_llm_hints_cannot_satisfy_required_typed_keys(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "graph.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXBTC-26MAY2517",
                "ticker": "KXBTC-26MAY2517-T86249.99",
                "title": "BTC threshold",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {"settlement_rules_text": None, "settlement_source_kind": "unknown"},
                "graph_hints": [
                    {"hint": "asset:BTC"},
                    {"hint": "price_source_index:CF Benchmarks BRTI"},
                    {"hint": "measurement_date:May 25, 2026"},
                ],
                "llm_relationship_hypotheses": [
                    {"typed_keys": {"asset": "BTC", "price_source_index": "CF Benchmarks"}}
                ],
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXBTC-26MAY2517-T86249.99")
    # Graph/LLM hints attached to the row must not satisfy typed keys.
    assert "price_source_index" in row["missing_typed_keys"]
    assert "measurement_date" in row["missing_typed_keys"]
    assert row["review_readiness_tier"] == TIER_DISCOVERY_READY


def test_all_outputs_are_diagnostic_only_no_paper_candidate_emitted(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "diag.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": "Federal Reserve's official website, Apr 28, 2027 meeting, 4.25%.",
                },
            }
        ],
    )

    report = _report(tmp_path)
    assert report["safety"]["paper_candidate_emitted"] is False
    assert report["safety"]["affects_evaluator_gates"] is False
    assert report["safety"]["family_classification_can_force_exact_payoff"] is False
    assert report["safety"]["title_similarity_can_promote_typed_keys"] is False
    assert report["safety"]["graph_or_llm_can_satisfy_typed_keys"] is False
    for row in report["markets"]:
        assert row["paper_candidate_emitted"] is False
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False


def test_politics_news_requires_explicit_source_url(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "politics.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXBULGARIAPRES",
                "ticker": "KXBULGARIAPRES-26-ABC",
                "title": "Will candidate X win?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {"settlement_rules_text": "Resolves per election board.", "settlement_source_kind": "rules_text_only"},
            }
        ],
    )

    report = _report(tmp_path)
    row = _find(report, "KXBULGARIAPRES-26-ABC")
    assert row["family"] == FAMILY_POLITICS_NEWS
    assert row["evidence_burden"] == BURDEN_HIGH_AMBIGUITY_STRICT_SOURCE_REQUIRED
    assert row["review_readiness_tier"] == TIER_DISCOVERY_READY
    assert "politics_news_requires_explicit_source" in row["blockers"]
    assert row["source_url_required_for_exact_evaluator"] is True


def test_cli_audit_settlement_evidence_burden_writes_report(tmp_path, capsys) -> None:
    _write_snapshot(
        tmp_path / "reports" / "cli.json",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-27APR",
                "ticker": "KXFED-27APR-T4.25",
                "title": "Will the upper bound be above 4.25%?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "settlement": {
                    "settlement_rules_text": (
                        "If the upper bound of the target federal funds rate is greater than 4.25% following the "
                        "Federal Reserve's Apr 28, 2027 meeting, market resolves Yes."
                    ),
                },
            }
        ],
    )
    json_output = tmp_path / "out.json"
    csv_output = tmp_path / "out.csv"
    markdown_output = tmp_path / "out.md"

    # Force registry-disabled so the assertion reflects the typed-only tier; the
    # CLI default would otherwise auto-load the example registry and promote
    # this Fed row to SETTLEMENT_SOURCE_REVIEW_READY via canonical-source match.
    missing_registry = tmp_path / "missing_registry.json"
    result = scan.main(
        [
            "audit-settlement-evidence-burden",
            "--input-dir",
            str(tmp_path / "reports"),
            "--json-output",
            str(json_output),
            "--csv-output",
            str(csv_output),
            "--markdown-output",
            str(markdown_output),
            "--registry-path",
            str(missing_registry),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "settlement_evidence_burden_status=OK" in stdout
    assert "family_typed_review=1" in stdout
    assert json_output.exists()
    assert csv_output.exists()
    assert markdown_output.exists()
