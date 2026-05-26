from __future__ import annotations

import json

import pytest

from graph_engine.reporting.platform_expansion_radar import (
    REQUIRED_FIELDS_TO_FETCH,
    build_platform_expansion_radar_report,
    validate_platform_expansion_radar_report,
    write_family_inference_audit_report,
    write_platform_expansion_radar_report,
)
from graph_engine.reporting.schema_validation import SchemaValidationError


def _indicator_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "signals": list(rows),
    }


def _signal(
    signal_id: str,
    signal_type: str,
    markets: list[str],
    *,
    venues: list[str] | None = None,
    severity: float = 80.0,
    confidence: str = "HIGH",
) -> dict:
    return {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "markets_involved": list(markets),
        "venues_involved": venues or sorted({market.split(":", 1)[0] for market in markets}),
        "relationship_evidence_type": "formula_diagnostic:fixture",
        "severity_score": severity,
        "confidence_tier": confidence,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "review_blockers": ["not_evaluator_input"],
    }


def _probability_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "probability_constraints": list(rows),
    }


def _constraint(constraint_id: str, constraint_type: str, markets: list[str]) -> dict:
    return {
        "constraint_id": constraint_id,
        "constraint_type": constraint_type,
        "markets_involved": list(markets),
        "venues_involved": sorted({market.split(":", 1)[0] for market in markets}),
        "severity_score": 70.0,
        "confidence_tier": "HIGH",
        "observed_gap": 0.12,
        "review_blockers": ["not_evaluator_input"],
    }


def _state_family_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "state_family_registry_entries": [
            {"formula_family": "BTC_THRESHOLD", "is_finite_state_safe": True},
            {"formula_family": "FED_MEETING_RANGE", "is_finite_state_safe": True},
            {"formula_family": "SPORTS_CHAMPION", "is_finite_state_safe": False},
        ],
    }


def _rv_packet_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "investigation_packets": [
            {
                "packet_id": "packet:btc",
                "signal_types": ["THRESHOLD_LADDER_INVERSION"],
                "markets_involved": ["kalshi:btc_100", "kalshi:btc_120"],
                "venues_involved": ["kalshi"],
                "priority_score": 90.0,
                "confidence_tier": "HIGH",
                "packet_blockers": ["graph_packet_review_only"],
            }
        ],
    }


def _ontology_row(
    entity_id: str,
    entity_type: str,
    canonical_name: str,
    *,
    confidence: str = "MEDIUM",
    aliases: list[str] | None = None,
    markets: list[str] | None = None,
    venues: list[str] | None = None,
    persistence_count: int | None = None,
) -> dict:
    row = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "canonical_name": canonical_name,
        "aliases": aliases or [],
        "source_market_ids": markets or [],
        "venues": venues or [],
        "evidence_type": "structured_formula",
        "confidence_tier": confidence,
        "blockers": [],
        "not_identity_proof_reason": None,
        "diagnostic_only": True,
    }
    if persistence_count is not None:
        row["persistence_count"] = persistence_count
    return row


def _ontology_report(*rows: dict, cross_venue_entity_candidates: list[str] | None = None) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "entity_count": len(rows),
        "ontology_rows": list(rows),
        "summary": {
            "entities_by_type": {},
            "low_confidence_entities": [],
            "cross_venue_entity_candidates": cross_venue_entity_candidates or [],
            "families_with_missing_entity_coverage": [],
            "recommended_next_entity_normalization_tasks": [],
        },
    }


def _tied_btc_fed_priority_inputs() -> dict:
    return {
        "trade_indicator_report": _indicator_report(
            _signal(
                "signal:btc",
                "THRESHOLD_LADDER_INVERSION",
                ["kalshi:btc_100", "kalshi:btc_120"],
                severity=10.0,
                confidence="LOW",
            ),
            _signal(
                "signal:fed",
                "RANGE_BUCKET_INCONSISTENCY",
                ["kalshi:fomc_450_475", "kalshi:fomc_475_500"],
                severity=10.0,
                confidence="LOW",
            ),
        ),
        "relative_value_reports": [
            {
                "platform_profiles": [
                    {"platform": "IBKR/ForecastEx", "family": "FED_MEETING_RANGE", "auth_required": True}
                ]
            }
        ],
    }


def _recommendation_identities(report: dict) -> list[tuple[str, str, str, str]]:
    return [
        (
            row["family"],
            row["missing_platform_or_venue"],
            row["expected_value_of_fetch"],
            row["allowed_next_action"],
        )
        for row in report["recommended_platform_fetches"]
    ]


def test_missing_relative_value_reports_dir_is_a_blocker_without_crashing(tmp_path) -> None:
    output = tmp_path / "market_graph_platform_expansion_radar.json"
    markdown = tmp_path / "market_graph_platform_expansion_radar.md"

    report = write_platform_expansion_radar_report(
        json_output=output,
        markdown_output=markdown,
        trade_indicators_path=tmp_path / "missing_indicators.json",
        probability_constraints_path=tmp_path / "missing_probability.json",
        rv_investigation_packets_path=tmp_path / "missing_packets.json",
        state_family_registry_path=tmp_path / "missing_registry.json",
        signal_persistence_path=tmp_path / "missing_persistence.json",
        relative_value_reports_dir=tmp_path / "missing_rv_reports",
    )

    assert output.exists()
    assert markdown.exists()
    assert "missing_relative_value_reports_dir" in report["blockers"]
    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    validate_platform_expansion_radar_report(json.loads(output.read_text(encoding="utf-8")))


def test_ontology_absent_keeps_existing_recommendation_ordering() -> None:
    report = build_platform_expansion_radar_report(**_tied_btc_fed_priority_inputs())

    assert report["ontology_report_used"] is False
    assert _recommendation_identities(report)[:2] == [
        ("FED_MEETING_RANGE", "ibkr_forecastex", "MEDIUM", "MANUAL_PLATFORM_REVIEW"),
        ("BTC_THRESHOLD", "polymarket", "MEDIUM", "FETCH_SAVED_MARKET_SNAPSHOT"),
    ]
    assert all(row["ontology_priority_score"] == 0 for row in report["platform_gap_rows"])
    assert all(row["ontology_priority_reasons"] == [] for row in report["platform_gap_rows"])
    validate_platform_expansion_radar_report(report)


def test_cross_venue_btc_ontology_priority_breaks_tied_platform_fetch_order() -> None:
    btc_entity_id = "entity:crypto_asset:btc"
    report = build_platform_expansion_radar_report(
        **_tied_btc_fed_priority_inputs(),
        event_entity_ontology_report=_ontology_report(
            _ontology_row(
                btc_entity_id,
                "CRYPTO_ASSET",
                "BTC",
                aliases=["Bitcoin"],
                markets=["kalshi:btc_100", "polymarket:btc_100"],
                venues=["kalshi", "polymarket"],
            ),
            cross_venue_entity_candidates=[btc_entity_id],
        ),
    )

    assert report["ontology_report_used"] is True
    assert _recommendation_identities(report)[:2] == [
        ("BTC_THRESHOLD", "polymarket", "MEDIUM", "FETCH_SAVED_MARKET_SNAPSHOT"),
        ("FED_MEETING_RANGE", "ibkr_forecastex", "MEDIUM", "MANUAL_PLATFORM_REVIEW"),
    ]
    btc_fetch = report["recommended_platform_fetches"][0]
    assert btc_fetch["ontology_priority_score"] == 1
    assert btc_fetch["ontology_priority_reasons"] == ["cross_venue_entity_candidate"]
    validate_platform_expansion_radar_report(report)


def test_ontology_with_no_relevant_overlap_keeps_recommendation_ordering() -> None:
    baseline = build_platform_expansion_radar_report(**_tied_btc_fed_priority_inputs())
    report = build_platform_expansion_radar_report(
        **_tied_btc_fed_priority_inputs(),
        event_entity_ontology_report=_ontology_report(
            _ontology_row(
                "entity:crypto_asset:eth",
                "CRYPTO_ASSET",
                "ETH",
                confidence="HIGH",
                aliases=["Ethereum"],
                markets=["kalshi:eth_5000", "polymarket:eth_5000"],
                venues=["kalshi", "polymarket"],
            ),
            cross_venue_entity_candidates=["entity:crypto_asset:eth"],
        ),
    )

    assert report["ontology_report_used"] is True
    assert _recommendation_identities(report) == _recommendation_identities(baseline)
    assert all(row["ontology_priority_score"] == 0 for row in report["platform_gap_rows"])
    validate_platform_expansion_radar_report(report)


def test_ontology_high_confidence_and_persistence_add_family_priority() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:btc",
                "THRESHOLD_LADDER_INVERSION",
                ["kalshi:btc_100", "kalshi:btc_120"],
                severity=10.0,
                confidence="LOW",
            )
        ),
        event_entity_ontology_report=_ontology_report(
            _ontology_row(
                "entity:crypto_asset:btc",
                "CRYPTO_ASSET",
                "BTC",
                confidence="HIGH",
                aliases=["Bitcoin"],
                markets=["kalshi:btc_100"],
                venues=["kalshi"],
                persistence_count=2,
            )
        ),
    )

    row = next(row for row in report["platform_gap_rows"] if row["family"] == "BTC_THRESHOLD")
    assert row["ontology_priority_score"] == 2
    assert row["ontology_priority_reasons"] == ["high_confidence_entity", "persistent_entity"]
    validate_platform_expansion_radar_report(report)


def test_missing_relative_value_reports_dir_not_provided_is_not_a_blocker(tmp_path) -> None:
    # When the operator does not pass --relative-value-reports-dir at all, the
    # platform radar runs without RV enrichment. That is the expected default
    # state, so it must not produce a noisy "blocker" entry in the daily report.
    output = tmp_path / "market_graph_platform_expansion_radar.json"
    markdown = tmp_path / "market_graph_platform_expansion_radar.md"

    report = write_platform_expansion_radar_report(
        json_output=output,
        markdown_output=markdown,
        trade_indicators_path=tmp_path / "missing_indicators.json",
        probability_constraints_path=tmp_path / "missing_probability.json",
        rv_investigation_packets_path=tmp_path / "missing_packets.json",
        state_family_registry_path=tmp_path / "missing_registry.json",
        signal_persistence_path=tmp_path / "missing_persistence.json",
        relative_value_reports_dir=None,
    )

    assert "relative_value_reports_dir_not_provided" not in report["blockers"]
    validate_platform_expansion_radar_report(json.loads(output.read_text(encoding="utf-8")))


def test_family_inference_uses_word_boundary_tokens() -> None:
    # Substring matching previously meant any text containing "btc" anywhere
    # (e.g. "abctc", "robotic") would be classified as BTC_THRESHOLD. The new
    # implementation requires a word-boundary hit. State-family-registry seeding
    # is intentionally omitted so we exercise the inference path directly.
    from graph_engine.reporting.platform_expansion_radar import _infer_family

    assert _infer_family({"signal_id": "robotic_announcements", "markets_involved": ["kalshi:robotic_announcements"]}) != "BTC_THRESHOLD"
    assert _infer_family({"signal_id": "btc_threshold_120k", "markets_involved": ["kalshi:btc_over_120k"]}) == "BTC_THRESHOLD"


def test_generic_threshold_market_formulas_do_not_classify_as_btc_threshold() -> None:
    # Regression: probability_constraints (and basis-risk packets) now attach a
    # market_formulas list that universally surfaces "threshold" and a
    # GENERIC_THRESHOLD family label for any threshold-shaped market. The radar
    # must not classify those non-BTC threshold markets (OpenAI valuation, AGI,
    # etc.) as BTC_THRESHOLD via the fallback rule, because doing so silently
    # adds polymarket/manifold/kalshi to BTC_THRESHOLD venues and erases the
    # legitimate BTC_THRESHOLD -> polymarket gap-row recommendation.
    from graph_engine.reporting.platform_expansion_radar import _infer_family

    openai_constraint_row = {
        "constraint_id": "probability:subset_superset:edge_openai_1t_subset_openai_500b",
        "constraint_type": "subset_superset",
        "markets_involved": ["polymarket:openai_valuation_1t_2027", "polymarket:openai_valuation_500b_2027"],
        "market_formulas": [
            {
                "market_id": "polymarket:openai_valuation_1t_2027",
                "family": "GENERIC_THRESHOLD",
                "asset": "openai_valuation_usd",
                "threshold": 1_000_000_000_000.0,
                "comparator": ">=",
                "source": "credible_business_press_or_filing",
                "date": "2027-12-31",
            },
            {
                "market_id": "polymarket:openai_valuation_500b_2027",
                "family": "GENERIC_THRESHOLD",
                "asset": "openai_valuation_usd",
                "threshold": 500_000_000_000.0,
                "comparator": ">=",
                "source": "credible_business_press_or_filing",
                "date": "2027-12-31",
            },
        ],
    }
    assert _infer_family(openai_constraint_row) != "BTC_THRESHOLD"


def test_family_inference_audit_lists_every_row_and_unknown_reason(tmp_path) -> None:
    output = tmp_path / "family_inference_audit.json"
    audit = write_family_inference_audit_report(
        {
            "trade_indicator_report": _indicator_report(
                _signal("signal:btc", "THRESHOLD_LADDER_INVERSION", ["kalshi:btc_100", "kalshi:btc_120"]),
                _signal("signal:unknown", "EVENT_FAMILY_OUTLIER", ["kalshi:unclassified_market"]),
            ),
            "probability_constraints_report": _probability_report(
                _constraint("constraint:fed", "range_bucket_partition", ["kalshi:fomc_450_475"])
            ),
            "rv_investigation_packets_report": _rv_packet_report(),
        },
        output,
    )

    assert output.exists()
    rows = audit["family_inference_audit"]
    assert audit["diagnostic_only"] is True
    assert audit["affects_evaluator_gates"] is False
    assert audit["row_count"] == 4
    assert {row["row_id"] for row in rows} == {"signal:btc", "signal:unknown", "constraint:fed", "packet:btc"}
    unknown = next(row for row in rows if row["row_id"] == "signal:unknown")
    assert unknown["inferred_family"] == "UNKNOWN"
    assert "no_supported_family_tokens" in unknown["reasons"]
    assert json.loads(output.read_text(encoding="utf-8")) == audit


def test_family_inference_audit_keeps_generic_threshold_unknown(tmp_path) -> None:
    output = tmp_path / "family_inference_audit.json"
    audit = write_family_inference_audit_report(
        {
            "probability_constraints": [
                {
                    "constraint_id": "constraint:generic_threshold",
                    "constraint_type": "subset_superset",
                    "markets_involved": ["polymarket:openai_valuation_1t_2027"],
                    "market_formulas": [
                        {
                            "market_id": "polymarket:openai_valuation_1t_2027",
                            "family": "GENERIC_THRESHOLD",
                            "asset": "openai_valuation_usd",
                            "threshold": 1_000_000_000_000.0,
                            "comparator": ">=",
                            "source": "credible_business_press_or_filing",
                            "date": "2027-12-31",
                        }
                    ],
                }
            ]
        },
        output,
    )
    row = audit["family_inference_audit"][0]

    assert row["row_id"] == "constraint:generic_threshold"
    assert row["inferred_family"] == "UNKNOWN"
    assert "generic_threshold_without_btc_token" in row["reasons"]
    assert "btc" not in row["matched_tokens"]


def test_platform_gap_rows_are_generated_from_fixture_like_graph_reports() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc", "THRESHOLD_LADDER_INVERSION", ["kalshi:btc_100", "kalshi:btc_120"]),
            _signal("signal:fed", "RANGE_BUCKET_INCONSISTENCY", ["kalshi:fomc_450_475", "kalshi:fomc_475_500"]),
        ),
        probability_constraints_report=_probability_report(
            _constraint("constraint:btc", "threshold_ladder", ["kalshi:btc_100", "kalshi:btc_120"])
        ),
        rv_investigation_packets_report=_rv_packet_report(),
        state_family_registry_report=_state_family_report(),
    )

    rows = {(row["family"], row["missing_platform_or_venue"]): row for row in report["platform_gap_rows"]}
    assert ("BTC_THRESHOLD", "polymarket") in rows
    assert ("FED_MEETING_RANGE", "polymarket") in rows
    assert rows[("BTC_THRESHOLD", "polymarket")]["expected_value_of_fetch"] == "HIGH"
    assert rows[("BTC_THRESHOLD", "polymarket")]["required_fields_to_fetch"] == REQUIRED_FIELDS_TO_FETCH
    validate_platform_expansion_radar_report(report)


def test_sports_game_level_sx_rows_recommend_matching_kalshi_and_polymarket_snapshots() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:sports", "EVENT_FAMILY_OUTLIER", ["kalshi:cleveland_futures"], venues=["kalshi"])
        ),
        state_family_registry_report=_state_family_report(),
        relative_value_reports=[
            {
                "rows": [
                    {
                        "platform": "SX Bet",
                        "family": "SPORTS_GAME_LEVEL",
                        "market_id": "sx:cleveland_game",
                    }
                ]
            }
        ],
    )

    sports_rows = [row for row in report["platform_gap_rows"] if row["family"] == "SPORTS_GAME_LEVEL"]
    assert {row["missing_platform_or_venue"] for row in sports_rows} == {"kalshi", "polymarket"}
    assert all("sports_game_level_vs_futures_scope_mismatch" in row["fake_edge_risks"] for row in sports_rows)
    assert all("game-level snapshots" in row["opportunity_reason"] for row in sports_rows)


def test_auth_required_platforms_are_manual_platform_review_not_adapter_ready() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:fed", "RANGE_BUCKET_INCONSISTENCY", ["kalshi:fomc_450_475"])
        ),
        state_family_registry_report=_state_family_report(),
        relative_value_reports=[
            {
                "platform_profiles": [
                    {"platform": "IBKR/ForecastEx", "family": "FED_MEETING_RANGE", "auth_required": True},
                    {"platform": "ProphetX", "family": "SPORTS_GAME_LEVEL", "requires_auth_review": True},
                ]
            }
        ],
    )

    manual_rows = {
        row["missing_platform_or_venue"]: row
        for row in report["platform_gap_rows"]
        if row["allowed_next_action"] == "MANUAL_PLATFORM_REVIEW"
    }
    assert {"ibkr_forecastex", "prophetx"} <= set(manual_rows)
    assert all("requires_auth_review" in row["fake_edge_risks"] for row in manual_rows.values())


def test_crypto_profile_only_recommends_fixture_backed_read_only_adapter() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc", "THRESHOLD_LADDER_INVERSION", ["kalshi:btc_100", "kalshi:btc_120"])
        ),
        relative_value_reports=[
            {
                "platform_profiles": [
                    {
                        "platform": "Crypto.com Predict CDNA",
                        "family": "BTC_THRESHOLD",
                        "profile_only": True,
                    }
                ]
            }
        ],
    )

    row = next(row for row in report["platform_gap_rows"] if row["missing_platform_or_venue"] == "crypto_com_predict_cdna")
    assert row["allowed_next_action"] == "BUILD_FIXTURE_FIRST"
    assert "platform_profile_only" in row["fake_edge_risks"]


def test_odds_api_reference_only_row_routes_to_ignore_low_value() -> None:
    report = build_platform_expansion_radar_report(
        relative_value_reports=[
            {
                "platform_profiles": [
                    {
                        "platform": "The Odds API",
                        "family": "SPORTS_GAME_LEVEL",
                        "reference_only_source": True,
                    }
                ]
            }
        ],
    )

    row = next(row for row in report["platform_gap_rows"] if row["missing_platform_or_venue"] == "the_odds_api")
    assert row["expected_value_of_fetch"] == "LOW"
    assert row["allowed_next_action"] == "IGNORE_LOW_VALUE"
    assert "fair_value_reference_only_not_executable_leg" in row["opportunity_reason"]
    assert "reference_only_source" in row["fake_edge_risks"]
    assert row not in [
        candidate
        for candidate in report["platform_gap_rows"]
        if candidate["allowed_next_action"] == "FETCH_SAVED_MARKET_SNAPSHOT"
        and candidate["missing_platform_or_venue"] == "the_odds_api"
    ]
    validate_platform_expansion_radar_report(report)


def test_allowed_actions_and_next_actions_remain_diagnostic_only() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc", "THRESHOLD_LADDER_INVERSION", ["kalshi:btc_100", "kalshi:btc_120"])
        )
    )

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for row in report["platform_gap_rows"]:
        assert row["diagnostic_only"] is True
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert row["allowed_next_action"] in {
            "FETCH_SAVED_MARKET_SNAPSHOT",
            "BUILD_READ_ONLY_ADAPTER",
            "BUILD_FIXTURE_FIRST",
            "MANUAL_PLATFORM_REVIEW",
            "IGNORE_LOW_VALUE",
        }
        assert row["allowed_next_action"] not in {
            "PAPER_" + "CANDIDATE",
            "TR" + "ADE",
            "EXE" + "CUTE",
            "ORD" + "ER",
            "B" + "UY",
            "S" + "ELL",
        }
    validate_platform_expansion_radar_report(report)


def test_radar_report_omits_restricted_action_vocabulary() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc", "THRESHOLD_LADDER_INVERSION", ["kalshi:btc_100", "kalshi:btc_120"])
        )
    )
    rendered = json.dumps(report, sort_keys=True)

    assert all(token not in rendered for token in ["PAPER_" + "CANDIDATE", "TR" + "ADE", "EXE" + "CUTE"])


def test_validator_rejects_prohibited_permission_values() -> None:
    report = build_platform_expansion_radar_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc", "THRESHOLD_LADDER_INVERSION", ["kalshi:btc_100", "kalshi:btc_120"])
        )
    )
    report["platform_gap_rows"][0]["allowed_next_action"] = "B" + "UY"

    with pytest.raises(SchemaValidationError):
        validate_platform_expansion_radar_report(report)
