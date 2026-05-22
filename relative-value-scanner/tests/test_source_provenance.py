from __future__ import annotations

import json
from pathlib import Path

from relative_value.fees import FlatFeeModel, PolymarketConservativeFeeModel
from relative_value.provenance import STATIC_FIXTURE, build_fixture_scan_provenance, source_readiness_report
from scan import (
    _OVERLAP_QUERY_PROFILES,
    _diagnostic_entities,
    build_executable_venue_readiness_report,
    build_live_matching_diagnostics_report,
    build_live_match_candidate_enrichment_report,
    build_non_sports_near_miss_diagnostics_report,
    build_live_overlap_universe_report,
    build_live_overlap_sweep_report,
    build_live_source_inventory_report,
    build_live_snapshot_inspection_report,
    build_live_readonly_match_report,
    build_source_smoke_report,
    diagnose_live_matching,
    diagnose_non_sports_near_misses,
    discover_live_source_inventory,
    fetch_live_overlap_universe,
    fetch_live_readonly,
    executable_venue_readiness,
    inspect_live_snapshots,
    main,
    match_live_readonly_snapshots,
    source_smoke,
    sweep_live_overlap_universe,
)


def test_fixture_scan_provenance_labels_static_fixture(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    provenance = build_fixture_scan_provenance(Path("venues") / "fixtures")

    assert provenance["data_source_mode"] == STATIC_FIXTURE
    assert provenance["live_fetch_attempted"] is False
    assert provenance["live_fetch_succeeded"] is False
    by_source = {source["source_id"]: source for source in provenance["sources"]}
    assert by_source["kalshi"]["snapshot_path"].endswith("kalshi_markets.json")
    assert by_source["polymarket"]["snapshot_path"].endswith("polymarket_markets.json")
    assert by_source["the_odds_api"]["source_type"] == "REFERENCE_ONLY"
    assert by_source["the_odds_api"]["requires_api_key"] is True
    assert by_source["the_odds_api"]["api_key_configured"] is False
    assert by_source["kalshi"]["live_fetch_attempted"] is False


def test_source_readiness_reports_missing_odds_key_without_secret(monkeypatch, capsys) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    result = main(["source-readiness"])

    output = capsys.readouterr().out
    assert result == 0
    assert "source_readiness_status=OK" in output
    assert "source=the_odds_api" in output
    assert "api_key_env_var=THE_ODDS_API_KEY" in output
    assert "api_key_configured=false" in output


def test_source_readiness_does_not_print_api_key_value(monkeypatch, capsys) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "super-secret-test-key")

    result = main(["source-readiness"])

    output = capsys.readouterr().out
    assert result == 0
    assert "api_key_configured=true" in output
    assert "super-secret-test-key" not in output


def test_source_readiness_rows_cover_requested_checklist_sources(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    report = source_readiness_report(env={})
    rows = {row["display_name"]: row for row in report["rows"]}

    assert {
        "Kalshi",
        "Polymarket",
        "The Odds API",
        "SX Bet",
        "ProphetX",
        "IBKR / ForecastEx",
        "Crypto.com",
        "Robinhood",
    }.issubset(rows)
    assert rows["The Odds API"]["source_type"] == "REFERENCE_ONLY"
    assert rows["The Odds API"]["can_create_paper_candidate"] is False
    assert rows["Kalshi"]["can_participate_in_candidate_pair"] is True
    assert rows["Kalshi"]["can_create_paper_candidate"] is False
    assert rows["Polymarket"]["can_participate_in_candidate_pair"] is True
    assert rows["Polymarket"]["can_create_paper_candidate"] is False
    assert rows["SX Bet"]["can_create_paper_candidate"] is False
    assert rows["ProphetX"]["source_mode_currently_used"] == "NOT_IMPLEMENTED"


def test_source_readiness_optional_json_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    output = tmp_path / "source_readiness.json"

    result = main(["source-readiness", "--output", str(output)])

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["default_scan_data_source_mode"] == "STATIC_FIXTURE"
    assert any(row["source_id"] == "the_odds_api" for row in payload["rows"])


def test_source_smoke_reports_missing_odds_key_safely(monkeypatch, capsys) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    _patch_public_clients(monkeypatch)

    result = source_smoke(load_env_file=False)

    output = capsys.readouterr().out
    assert result == 0
    assert "source_smoke_status=OK" in output
    assert "source_id=the_odds_api" in output
    assert "expected_env_vars=THE_ODDS_API_KEY" in output
    assert "env_configured=false" in output
    assert "error_category=MISSING_ENV" in output


def test_source_smoke_does_not_print_configured_key_value(monkeypatch, capsys) -> None:
    secret = "super-secret-source-smoke-key"
    monkeypatch.setenv("THE_ODDS_API_KEY", secret)
    _patch_public_clients(monkeypatch)
    monkeypatch.setattr("scan.TheOddsApiReadOnlyClient", _FakeOddsClient)

    result = source_smoke(load_env_file=False)

    output = capsys.readouterr().out
    assert result == 0
    assert "source_id=the_odds_api" in output
    assert "env_configured=true" in output
    assert secret not in output


def test_source_smoke_keeps_planned_sources_not_live_connected(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    _patch_public_clients(monkeypatch)

    report = build_source_smoke_report()
    rows = {row["source_id"]: row for row in report["rows"]}

    for source_id in ("sx_bet", "forecastex_ibkr", "prophetx", "crypto_com", "robinhood"):
        assert rows[source_id]["implementation_status"] == "PLANNED_NOT_IMPLEMENTED"
        assert rows[source_id]["live_fetch_implemented"] is False
        assert rows[source_id]["live_fetch_attempted"] is False
        assert rows[source_id]["live_fetch_succeeded"] is False
        assert rows[source_id]["error_category"] == "LIVE_FETCH_NOT_IMPLEMENTED"


def test_source_smoke_failed_source_does_not_crash_whole_report(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.setattr("scan.PolymarketGammaClient", _FakePolymarketClient)
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _FailingKalshiClient)

    result = source_smoke(load_env_file=False)

    assert result == 0


def test_the_odds_api_source_smoke_remains_reference_only(monkeypatch) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "super-secret-test-key")
    _patch_public_clients(monkeypatch)
    monkeypatch.setattr("scan.TheOddsApiReadOnlyClient", _FakeOddsClient)

    report = build_source_smoke_report()
    rows = {row["source_id"]: row for row in report["rows"]}

    assert rows["the_odds_api"]["source_type"] == "REFERENCE_ONLY"
    assert rows["the_odds_api"]["can_create_paper_candidate"] is False
    assert rows["the_odds_api"]["live_fetch_succeeded"] is True


def test_source_smoke_separates_pair_participation_from_paper_candidate_creation(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    _patch_public_clients(monkeypatch)

    report = build_source_smoke_report()
    rows = {row["source_id"]: row for row in report["rows"]}

    assert rows["kalshi"]["can_participate_in_candidate_pair"] is True
    assert rows["kalshi"]["can_create_paper_candidate"] is False
    assert rows["polymarket"]["can_participate_in_candidate_pair"] is True
    assert rows["polymarket"]["can_create_paper_candidate"] is False


def test_executable_venue_readiness_rows_are_conservative(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    report = build_executable_venue_readiness_report(env={})
    rows = {row["source_id"]: row for row in report["rows"]}

    assert report["default_scan_data_source_mode"] == "STATIC_FIXTURE"
    assert report["live_api_fetch_attempted"] is False
    assert report["recommended_next_adapter_candidate"]["source_id"] == "sx_bet"
    assert "research fetch exists" in report["recommended_next_adapter_candidate"]["rationale"]
    assert "candidate-eligible normalized adapter" in report["recommended_next_adapter_candidate"]["rationale"]
    assert rows["kalshi"]["live_readonly_research_fetch_exists"] is True
    assert rows["kalshi"]["live_readonly_candidate_adapter_exists"] is True
    assert rows["kalshi"]["live_readonly_adapter_exists"] is True
    assert rows["kalshi"]["env_configured"] == "not_applicable"
    assert rows["polymarket"]["live_readonly_research_fetch_exists"] is True
    assert rows["polymarket"]["live_readonly_candidate_adapter_exists"] is True
    assert rows["polymarket"]["live_readonly_adapter_exists"] is True
    assert rows["polymarket"]["env_configured"] == "not_applicable"
    assert rows["sx_bet"]["implementation_status"] == "PLANNED_NOT_IMPLEMENTED"
    assert rows["sx_bet"]["env_configured"] == "not_applicable"
    assert rows["sx_bet"]["live_readonly_research_fetch_exists"] is True
    assert rows["sx_bet"]["live_readonly_candidate_adapter_exists"] is False
    assert rows["sx_bet"]["live_readonly_adapter_exists"] is False
    assert rows["sx_bet"]["can_create_candidate_pair_now"] is False
    assert rows["forecastex_ibkr"]["implementation_status"] == "PLANNED_NOT_IMPLEMENTED"
    assert rows["prophetx"]["implementation_status"] == "NOT_IMPLEMENTED"
    assert rows["crypto_com"]["source_type"] == "DO_NOT_USE_YET"
    assert rows["robinhood"]["source_type"] == "DO_NOT_USE_YET"
    assert rows["the_odds_api"]["source_type"] == "REFERENCE_ONLY"
    assert rows["the_odds_api"]["live_readonly_research_fetch_exists"] is True
    assert rows["the_odds_api"]["live_readonly_candidate_adapter_exists"] is False
    assert rows["the_odds_api"]["live_readonly_adapter_exists"] is False
    assert rows["the_odds_api"]["can_create_candidate_pair_now"] is False
    assert all(row["can_create_paper_candidate_now"] is False for row in rows.values())
    assert all(row["execution_allowed_in_project_now"] is False for row in rows.values())


def test_executable_venue_readiness_command_writes_reports_without_secret(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "super-secret-readiness-key"
    monkeypatch.setenv("THE_ODDS_API_KEY", secret)

    result = executable_venue_readiness(
        json_output=tmp_path / "executable_readiness.json",
        markdown_output=tmp_path / "executable_readiness.md",
        load_env_file=False,
    )

    output = capsys.readouterr().out
    report_text = (
        (tmp_path / "executable_readiness.json").read_text(encoding="utf-8")
        + (tmp_path / "executable_readiness.md").read_text(encoding="utf-8")
        + output
    )
    payload = json.loads((tmp_path / "executable_readiness.json").read_text(encoding="utf-8"))
    rows = {row["source_id"]: row for row in payload["rows"]}
    assert result == 0
    assert "executable_venue_readiness_status=OK" in output
    assert "live_readonly_research_fetch_exists=true" in output
    assert "live_readonly_candidate_adapter_exists=false" in output
    assert rows["the_odds_api"]["env_configured"] is True
    assert secret not in report_text
    assert "POSSIBLE_ARB" not in report_text


def test_executable_venue_readiness_loads_local_env_safely(tmp_path: Path, monkeypatch) -> None:
    secret = "super-secret-dot-env-key"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(f"THE_ODDS_API_KEY={secret}\n", encoding="utf-8")

    result = executable_venue_readiness(
        json_output=tmp_path / "executable_readiness.json",
        markdown_output=tmp_path / "executable_readiness.md",
    )

    payload = json.loads((tmp_path / "executable_readiness.json").read_text(encoding="utf-8"))
    report_text = (
        (tmp_path / "executable_readiness.json").read_text(encoding="utf-8")
        + (tmp_path / "executable_readiness.md").read_text(encoding="utf-8")
    )
    rows = {row["source_id"]: row for row in payload["rows"]}
    assert result == 0
    assert rows["the_odds_api"]["env_configured"] is True
    assert secret not in report_text


def test_discover_live_source_inventory_writes_human_review_report(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _InventoryKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _InventoryPolymarketClient)

    result = discover_live_source_inventory(
        limit=50,
        timeout_seconds=1.0,
        json_output=tmp_path / "inventory.json",
        markdown_output=tmp_path / "inventory.md",
    )

    output = capsys.readouterr().out
    report_text = (tmp_path / "inventory.json").read_text(encoding="utf-8") + (tmp_path / "inventory.md").read_text(encoding="utf-8") + output
    payload = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    assert result == 0
    assert "live_source_inventory_status=OK" in output
    assert payload["default_scan_data_source_mode"] == "STATIC_FIXTURE"
    assert payload["profile_table_modified"] is False
    assert payload["sources"]["kalshi"]["record_count"] == 5
    assert payload["sources"]["polymarket"]["record_count"] == 4
    assert payload["analysis"]["dead_or_guessed_profiles_to_recheck"]["KXAI"]["status"] == "confirmed_absent"
    assert payload["analysis"]["dead_or_guessed_profiles_to_recheck"]["KXPRES"]["status"] == "confirmed_absent"
    assert payload["analysis"]["dead_or_guessed_profiles_to_recheck"]["KXNVDA"]["status"] == "confirmed_absent"
    assert any(row["category"] == "macro" for row in payload["analysis"]["candidate_profile_suggestions"])
    assert "PAPER_CANDIDATE" not in report_text
    assert "POSSIBLE_ARB" not in report_text


def test_live_source_inventory_suggestions_sort_by_active_market_count() -> None:
    report = build_live_source_inventory_report(
        limit=500,
        kalshi_client=_InventoryKalshiClient(),
        polymarket_client=_InventoryPolymarketClient(),
    )

    suggestions = {row["category"]: row for row in report["analysis"]["candidate_profile_suggestions"]}
    macro = suggestions["macro"]
    crypto = suggestions["crypto"]

    assert macro["kalshi_series_candidates"][:2] == ["KXFED", "KXCPI"]
    assert [row["active_market_count"] for row in macro["kalshi_series_candidate_details"][:2]] == [12, 8]
    assert crypto["kalshi_series_candidates"][:2] == ["KXETH", "KXBTC"]
    assert crypto["polymarket_tag_candidates"][0] == "crypto"
    assert crypto["polymarket_tag_candidate_details"][0]["active_market_count"] == 30


def test_polymarket_bad_tag_label_skips_one_row_without_failing_inventory() -> None:
    report = build_live_source_inventory_report(
        limit=500,
        kalshi_client=_InventoryKalshiClient(),
        polymarket_client=_PolymarketTagsWithBadFallbackLabelClient(),
    )

    polymarket = report["sources"]["polymarket"]
    slugs = {row["tag_slug"] for row in polymarket["records"]}
    assert report["status"] == "OK"
    assert polymarket["status"] == "OK"
    assert "crypto" in slugs
    assert "open-ai" in slugs
    assert "bad-label-!!!" not in slugs


def test_polymarket_inventory_reports_possible_server_cap_at_100_tags() -> None:
    report = build_live_source_inventory_report(
        limit=500,
        kalshi_client=_InventoryKalshiClient(),
        polymarket_client=_HundredTagInventoryPolymarketClient(),
    )

    assert report["sources"]["polymarket"]["record_count"] == 100
    assert report["sources"]["polymarket"]["pagination_or_server_cap_possible"] is True


def test_inventory_profile_recheck_covers_all_overlap_kalshi_series() -> None:
    report = build_live_source_inventory_report(
        limit=500,
        kalshi_client=_InventoryKalshiClient(),
        polymarket_client=_InventoryPolymarketClient(),
    )
    expected_tickers = {
        str(ticker).upper()
        for profile in _OVERLAP_QUERY_PROFILES.values()
        for ticker in profile.get("kalshi_series_tickers", ())
    }

    recheck = report["analysis"]["overlap_profile_kalshi_series_recheck"]
    assert expected_tickers
    assert set(recheck) == expected_tickers
    assert recheck["KXFED"]["status"] == "confirmed_present"
    assert recheck["KXCPI"]["status"] == "confirmed_present"
    assert recheck["KXBTC"]["status"] == "confirmed_present"
    assert recheck["KXETH"]["status"] == "confirmed_present"
    assert recheck["KXTSLA"]["status"] == "confirmed_present"
    assert report["analysis"]["dead_or_guessed_profiles_to_recheck"]["KXAI"]["status"] == "confirmed_absent"
    assert "KXAI" not in recheck


def test_discover_live_source_inventory_failure_rows_do_not_crash(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _FailingInventoryKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _InventoryPolymarketClient)

    result = discover_live_source_inventory(
        limit=50,
        timeout_seconds=1.0,
        json_output=tmp_path / "inventory.json",
        markdown_output=tmp_path / "inventory.md",
    )

    output = capsys.readouterr().out
    payload = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    assert result == 0
    assert payload["status"] == "PARTIAL"
    assert payload["sources"]["kalshi"]["status"] == "FAILED"
    assert payload["sources"]["polymarket"]["status"] == "OK"
    assert "live_source_inventory_status=PARTIAL" in output


def test_discover_live_source_inventory_does_not_print_secret_values(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "super-secret-inventory-token"
    monkeypatch.setattr("scan.KalshiReadOnlyClient", lambda **kwargs: _SecretInventoryKalshiClient(secret))
    monkeypatch.setattr("scan.PolymarketGammaClient", _InventoryPolymarketClient)

    result = discover_live_source_inventory(
        limit=50,
        timeout_seconds=1.0,
        json_output=tmp_path / "inventory.json",
        markdown_output=tmp_path / "inventory.md",
    )

    output = capsys.readouterr().out
    report_text = (tmp_path / "inventory.json").read_text(encoding="utf-8") + (tmp_path / "inventory.md").read_text(encoding="utf-8") + output
    assert result == 0
    assert secret not in report_text


def test_build_live_source_inventory_report_can_fail_all_sources() -> None:
    report = build_live_source_inventory_report(
        kalshi_client=_FailingInventoryKalshiClient(),
        polymarket_client=_FailingInventoryPolymarketClient(),
    )

    assert report["status"] == "FAILED"
    assert report["sources"]["kalshi"]["status"] == "FAILED"
    assert report["sources"]["polymarket"]["status"] == "FAILED"
    assert report["profile_table_modified"] is False
    assert report["analysis"]["dead_or_guessed_profiles_to_recheck"]["KXAI"]["status"] == "unresolved"


def test_fetch_live_readonly_writes_manifest_and_safe_failure_rows(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _FakeKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _FakePolymarketClient)

    result = fetch_live_readonly(
        sources="kalshi,polymarket,the_odds_api,sx_bet",
        max_markets=2,
        timeout_seconds=1.0,
        the_odds_api_sport_key="basketball_nba",
        output_dir=tmp_path,
        load_env_file=False,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "fetch_live_readonly_status=OK" in output
    assert "THE_ODDS_API_KEY" not in output
    manifest = json.loads((tmp_path / "live_readonly_manifest.json").read_text(encoding="utf-8"))
    rows = {row["source_id"]: row for row in manifest["rows"]}
    assert rows["kalshi"]["live_fetch_succeeded"] is True
    assert rows["polymarket"]["live_fetch_succeeded"] is True
    assert rows["the_odds_api"]["error_category"] == "MISSING_ENV"
    assert rows["sx_bet"]["error_category"] == "LIVE_FETCH_NOT_IMPLEMENTED"
    assert Path(rows["kalshi"]["snapshot_path"]).exists()
    assert Path(rows["the_odds_api"]["snapshot_path"]).exists()


def test_fetch_live_overlap_universe_filters_sports_and_writes_pipeline_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _OverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _OverlapPolymarketClient)

    result = fetch_live_overlap_universe(
        category="sports",
        query=None,
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path / "live_readonly",
        report_dir=tmp_path,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "live_overlap_universe_status=OK" in output
    assert "PAPER_CANDIDATE" not in output
    assert "POSSIBLE_ARB" not in output
    kalshi = json.loads((tmp_path / "live_readonly" / "kalshi_live_readonly_snapshot.json").read_text(encoding="utf-8"))
    polymarket = json.loads((tmp_path / "live_readonly" / "polymarket_live_readonly_snapshot.json").read_text(encoding="utf-8"))
    assert kalshi["normalized_count"] == 1
    assert polymarket["normalized_count"] == 1
    assert kalshi["overlap_universe"]["can_create_paper_candidate"] is False
    assert polymarket["overlap_universe"]["same_payoff_asserted"] is False
    report = json.loads((tmp_path / "live_overlap_universe_report.json").read_text(encoding="utf-8"))
    assert report["summary"]["retained_by_source"] == {"kalshi": 1, "polymarket": 1}
    assert report["summary"]["category_counts_by_source"]["kalshi"]["sports"] == 1
    assert report["safety"]["thresholds_changed"] is False
    assert report["safety"]["uses_reference_as_executable_leg"] is False


def test_fetch_live_overlap_universe_unrelated_inventory_reports_no_overlap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _UnrelatedKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _UnrelatedPolymarketClient)

    report = build_live_overlap_universe_report(
        category="all",
        query=None,
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert report["summary"]["retained_by_source"] == {"kalshi": 1, "polymarket": 1}
    assert report["summary"]["top_text_similarity"] < 0.68
    assert report["summary"]["raw_cross_source_candidate_comparisons"] == 1
    assert report["summary"]["recommended_next_query"]
    assert "PAPER" not in json.dumps(report)
    assert "POSSIBLE_ARB" not in json.dumps(report)


def test_fetch_live_overlap_universe_nba_query_uses_source_specific_targeting(tmp_path: Path, monkeypatch) -> None:
    _LeagueOverlapKalshiClient.last_kwargs = {}
    _LeagueOverlapKalshiClient.calls = []
    _LeagueOverlapPolymarketClient.last_kwargs = {}
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _LeagueOverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _LeagueOverlapPolymarketClient)

    report = build_live_overlap_universe_report(
        category="all",
        query="NBA",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert _LeagueOverlapKalshiClient.last_kwargs["series_ticker"] == "KXNBA"
    assert _LeagueOverlapPolymarketClient.last_kwargs["tag_slug"] == "nba"
    assert report["fetch"]["kalshi"]["direct_targeting"] == "series_ticker:KXNBA"
    assert report["fetch"]["polymarket"]["direct_targeting"] == "tag_slug:nba"
    assert report["summary"]["retained_by_source"] == {"kalshi": 1, "polymarket": 1}
    assert report["summary"]["filter_diagnostics_by_source"]["kalshi"]["mode"] == "advisory_local_filter_after_source_targeted_fetch"
    assert report["summary"]["filter_diagnostics_by_source"]["polymarket"]["mode"] == "advisory_local_filter_after_source_targeted_fetch"
    assert report["safety"]["thresholds_changed"] is False


def test_fetch_live_overlap_universe_mlb_query_uses_source_specific_targeting(tmp_path: Path, monkeypatch) -> None:
    _LeagueOverlapKalshiClient.last_kwargs = {}
    _LeagueOverlapKalshiClient.calls = []
    _LeagueOverlapPolymarketClient.last_kwargs = {}
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _LeagueOverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _LeagueOverlapPolymarketClient)

    report = build_live_overlap_universe_report(
        category="all",
        query="MLB",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert _LeagueOverlapKalshiClient.last_kwargs["series_ticker"] == "KXMLB"
    assert _LeagueOverlapPolymarketClient.last_kwargs["tag_slug"] == "mlb"
    assert report["fetch"]["kalshi"]["direct_targeting"] == "series_ticker:KXMLB"
    assert report["fetch"]["polymarket"]["direct_targeting"] == "tag_slug:mlb"
    assert report["summary"]["retained_by_source"] == {"kalshi": 1, "polymarket": 1}
    assert report["summary"]["filter_diagnostics_by_source"]["kalshi"]["sample_retained_markets"][0]["query_hit_terms"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)
    assert "POSSIBLE_ARB" not in json.dumps(report)


def test_fetch_live_overlap_universe_fed_query_attempts_kalshi_series_profiles(tmp_path: Path, monkeypatch) -> None:
    _LeagueOverlapKalshiClient.last_kwargs = {}
    _LeagueOverlapKalshiClient.calls = []
    _LeagueOverlapPolymarketClient.last_kwargs = {}
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _LeagueOverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _LeagueOverlapPolymarketClient)

    report = build_live_overlap_universe_report(
        category="macro",
        query="Fed",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert [call["series_ticker"] for call in _LeagueOverlapKalshiClient.calls] == [
        "KXFED",
        "FEDDECISION",
        "KXFOMCDISSENTCOUNT",
        "KXFOMCVOTE",
    ]
    assert report["fetch"]["kalshi"]["targeting_method"] == "series_based"
    assert report["fetch"]["kalshi"]["source_specific_profile_attempted"] is True
    assert report["fetch"]["kalshi"]["attempted_series_tickers"] == [
        "KXFED",
        "FEDDECISION",
        "KXFOMCDISSENTCOUNT",
        "KXFOMCVOTE",
    ]
    assert report["summary"]["source_targeting"]["kalshi"]["series_results"] == [
        {"series_ticker": "KXFED", "status": "OK", "result_count": 1},
        {"series_ticker": "FEDDECISION", "status": "OK", "result_count": 0},
        {"series_ticker": "KXFOMCDISSENTCOUNT", "status": "OK", "result_count": 0},
        {"series_ticker": "KXFOMCVOTE", "status": "OK", "result_count": 0},
    ]
    assert report["summary"]["retained_by_source"]["kalshi"] == 1
    assert report["summary"]["filter_diagnostics_by_source"]["kalshi"]["source_specific_profile_attempted"] is True
    assert "PAPER_CANDIDATE" not in json.dumps(report)
    assert "POSSIBLE_ARB" not in json.dumps(report)


def test_fetch_live_overlap_universe_ai_query_uses_non_sports_profile(tmp_path: Path, monkeypatch) -> None:
    _LeagueOverlapKalshiClient.last_kwargs = {}
    _LeagueOverlapKalshiClient.calls = []
    _LeagueOverlapPolymarketClient.last_kwargs = {}
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _LeagueOverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _LeagueOverlapPolymarketClient)

    report = build_live_overlap_universe_report(
        category="ai",
        query="AI",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert [call["series_ticker"] for call in _LeagueOverlapKalshiClient.calls] == [
        "AIDEBATES",
        "AILEGISLATION",
        "AITURING",
        "APPLEAI",
        "GPT45",
        "KXGPT5",
    ]
    assert _LeagueOverlapPolymarketClient.last_kwargs["tag_slug"] == "openai"
    assert report["fetch"]["kalshi"]["direct_targeting"] == "series_ticker:AIDEBATES,AILEGISLATION,AITURING,APPLEAI,GPT45,KXGPT5"
    assert report["fetch"]["polymarket"]["direct_targeting"] == "tag_slug:openai"
    assert report["summary"]["retained_by_source"] == {"kalshi": 1, "polymarket": 1}
    assert report["summary"]["source_targeting"]["kalshi"]["attempted_series_tickers"] == [
        "AIDEBATES",
        "AILEGISLATION",
        "AITURING",
        "APPLEAI",
        "GPT45",
        "KXGPT5",
    ]
    assert report["fetch"]["kalshi"]["profile_provenance"]["source_inventory_confirmed"] is True
    assert report["fetch"]["kalshi"]["profile_provenance"]["replaced_dead_series_tickers"] == ["KXAI"]
    assert report["safety"]["thresholds_changed"] is False


def test_fetch_live_overlap_universe_zero_retention_profile_reports_honestly(tmp_path: Path, monkeypatch) -> None:
    _LeagueOverlapKalshiClient.last_kwargs = {}
    _LeagueOverlapKalshiClient.calls = []
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _LeagueOverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _LeagueOverlapPolymarketClient)

    report = build_live_overlap_universe_report(
        category="macro",
        query="CPI",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert report["fetch"]["kalshi"]["attempted_series_tickers"] == ["KXCPI", "CPI", "CPIYOY", "CPICORE", "KXCOREUND"]
    assert report["summary"]["retained_by_source"]["kalshi"] == 0
    assert report["summary"]["source_targeting"]["kalshi"]["series_results"] == [
        {"series_ticker": "KXCPI", "status": "OK", "result_count": 0},
        {"series_ticker": "CPI", "status": "OK", "result_count": 0},
        {"series_ticker": "CPIYOY", "status": "OK", "result_count": 0},
        {"series_ticker": "CPICORE", "status": "OK", "result_count": 0},
        {"series_ticker": "KXCOREUND", "status": "OK", "result_count": 0},
    ]


def test_fetch_live_overlap_universe_unknown_profile_does_not_fake_source_targeting(tmp_path: Path, monkeypatch) -> None:
    _LeagueOverlapKalshiClient.last_kwargs = {}
    _LeagueOverlapKalshiClient.calls = []
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _LeagueOverlapKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _LeagueOverlapPolymarketClient)

    report = build_live_overlap_universe_report(
        category="all",
        query="unknown-profile",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path,
    )["report"]

    assert _LeagueOverlapKalshiClient.calls == [{"limit": 10, "max_pages": 1}]
    assert report["fetch"]["kalshi"]["direct_targeting"] == "broad_open_inventory_then_local_filter"
    assert report["fetch"]["kalshi"]["source_specific_profile_attempted"] is False
    assert report["summary"]["source_targeting"]["kalshi"]["attempted_series_tickers"] == []


def test_fetch_live_overlap_universe_query_filter_and_secret_redaction(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "super-secret-overlap-key"
    monkeypatch.setattr("scan.KalshiReadOnlyClient", lambda **kwargs: _SecretOverlapKalshiClient(secret))
    monkeypatch.setattr("scan.PolymarketGammaClient", _OverlapPolymarketClient)

    result = fetch_live_overlap_universe(
        category="all",
        query="NBA",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        output_dir=tmp_path / "live_readonly",
        report_dir=tmp_path,
    )

    output = capsys.readouterr().out
    snapshot_text = (tmp_path / "live_readonly" / "kalshi_live_readonly_snapshot.json").read_text(encoding="utf-8")
    assert result == 0
    assert secret not in output
    assert secret not in snapshot_text
    assert "[REDACTED]" in snapshot_text
    assert (tmp_path / "overlap_all_nba_live_overlap_universe_report.json").exists()


def test_sweep_live_overlap_universe_is_explicit_research_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _SweepKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _SweepPolymarketClient)

    report = build_live_overlap_sweep_report(
        categories="macro,crypto",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        snapshot_dir=tmp_path,
    )
    serialized = json.dumps(report)

    assert report["status"] == "OK"
    assert report["default_scan_data_source_mode"] == "STATIC_FIXTURE"
    assert report["default_scan_live_fetch_attempted"] is False
    assert report["summary"]["row_count"] == 9
    assert report["summary"]["kalshi_zero_retention_count"] == 4
    assert report["summary"]["polymarket_zero_retention_count"] == 6
    for row in report["rows"]:
        assert "\\sweep\\" in str(row["snapshot_dir"]) or "/sweep/" in str(row["snapshot_dir"])
    assert not (tmp_path / "kalshi_live_readonly_snapshot.json").exists()
    assert not (tmp_path / "polymarket_live_readonly_snapshot.json").exists()
    assert [row["query"] for row in build_live_overlap_sweep_report(
        categories="weather",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        snapshot_dir=tmp_path,
    )["rows"]] == ["weather"]
    assert report["safety"]["thresholds_changed"] is False
    assert report["safety"]["uses_the_odds_api_as_executable_leg"] is False
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_sweep_live_overlap_universe_empty_categories_do_not_crash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _SweepKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _SweepPolymarketClient)

    report = build_live_overlap_sweep_report(
        categories=",unknown,",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        snapshot_dir=tmp_path,
    )

    assert report["status"] == "NO_CATEGORIES"
    assert report["rows"] == []
    assert report["summary"]["row_count"] == 0


def test_sweep_live_overlap_universe_cli_writes_reports_without_secrets(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _SweepKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _SweepPolymarketClient)

    result = sweep_live_overlap_universe(
        categories="macro",
        max_markets=10,
        timeout_seconds=1.0,
        kalshi_max_pages=1,
        snapshot_dir=tmp_path / "live_readonly",
        json_output=tmp_path / "sweep.json",
        markdown_output=tmp_path / "sweep.md",
    )

    output = capsys.readouterr().out
    report_text = (tmp_path / "sweep.json").read_text(encoding="utf-8") + (tmp_path / "sweep.md").read_text(encoding="utf-8") + output
    assert result == 0
    assert "live_overlap_sweep_status=OK" in output
    assert "super-secret" not in report_text
    assert "PAPER_CANDIDATE" not in report_text
    assert "POSSIBLE_ARB" not in report_text


def test_enrich_live_match_candidates_touches_only_current_review_pairs(tmp_path: Path) -> None:
    _write_enrichment_inputs(tmp_path)
    kalshi_client = _FakeKalshiOrderbookClient()
    polymarket_client = _FakePolymarketOrderbookClient()

    report = build_live_match_candidate_enrichment_report(
        match_report_path=tmp_path / "live_readonly_match_report.json",
        snapshot_dir=tmp_path,
        timeout_seconds=1.0,
        max_snapshot_age_hours=24.0,
        kalshi_client=kalshi_client,
        polymarket_client=polymarket_client,
    )
    serialized = json.dumps(report)

    assert report["summary"]["pair_count"] == 1
    assert report["summary"]["selected_markets_by_source"] == {"kalshi": 1, "polymarket": 1}
    assert kalshi_client.tickers == ["KXNBA-1"]
    assert polymarket_client.token_ids == ["yes-token-1"]
    row = report["pairs"][0]
    assert row["enrichment"]["depth_available"] is True
    assert row["enrichment"]["quote_timestamp_available"] is True
    assert row["enrichment"]["fees_available"] is True
    assert row["enrichment"]["kalshi_fee_model_status"] == "reviewed_conservative"
    assert row["enrichment"]["polymarket_fee_model_status"] == "reviewed_official_category_schedule_2026_05_22"
    assert row["enrichment"]["polymarket_fee_source_used"] == "official_category_schedule"
    assert row["enrichment"]["polymarket_fee_source"] == "https://docs.polymarket.com/trading/fees"
    assert row["enrichment"]["polymarket_fee_source_version"] == "official_category_schedule_2026_05_22"
    assert row["enrichment"]["polymarket_fee_category"] == "sports"
    assert row["enrichment"]["polymarket_fee_rate_used"] == 0.03
    assert row["enrichment"]["polymarket_maker_fee_rate"] == 0.0
    assert row["enrichment"]["polymarket_maker_fee_used_for_diagnostic"] is False
    assert row["enrichment"]["polymarket_taker_fee_used_for_diagnostic"] is True
    assert row["enrichment"]["polymarket_fee_assumption_type"] == "taker_fee_official_category_schedule_conservative"
    assert row["enrichment"]["fee_blocker_reason"] is None
    assert row["enrichment"]["gross_gap_cents"] is not None
    assert row["gross_gap_caveat"] == "same_payoff=false; gross_gap_cents is not arb edge"
    assert row["enrichment"]["gross_gap_caveat"] == "same_payoff=false; gross_gap_cents is not arb edge"
    assert row["enrichment"]["fee_adjusted_gap_cents"] is not None
    assert row["enrichment"]["kalshi_orderbook_fetched_at"]
    assert row["enrichment"]["polymarket_orderbook_fetched_at"]
    assert row["enrichment"]["kalshi_quote_age_seconds"] is not None
    assert row["enrichment"]["polymarket_quote_age_seconds"] is not None
    assert row["enrichment"]["kalshi_bid_source"] == "orderbook_fetch"
    assert row["enrichment"]["polymarket_ask_source"] == "orderbook_fetch"
    assert "missing_orderbook_depth" in row["resolved_research_blockers"]
    assert "missing_quote_timestamp" in row["resolved_research_blockers"]
    assert "missing_fees" in row["resolved_research_blockers"]
    assert "missing_fees" not in row["remaining_research_blockers"]
    assert "relationship_manual_review_required" in row["remaining_research_blockers"]
    assert report["summary"]["fees_available_count"] == 1
    assert report["safety"]["uses_the_odds_api_as_executable_leg"] is False
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_polymarket_fee_model_uses_conservative_nonzero_rates() -> None:
    model = PolymarketConservativeFeeModel()

    assert model.rate_for_category("sports") == 0.03
    assert model.fee_for_leg_for_category(0.41, "sports") > 0
    assert model.category_key("unreviewed-category") == "other_general"
    assert model.rate_for_category("unreviewed-category") == 0.05


def test_enrich_live_match_candidates_unreviewed_polymarket_fee_keeps_fee_blocker(tmp_path: Path) -> None:
    _write_enrichment_inputs(tmp_path)

    report = build_live_match_candidate_enrichment_report(
        match_report_path=tmp_path / "live_readonly_match_report.json",
        snapshot_dir=tmp_path,
        timeout_seconds=1.0,
        max_snapshot_age_hours=24.0,
        kalshi_client=_FakeKalshiOrderbookClient(),
        polymarket_client=_FakePolymarketOrderbookClient(),
        polymarket_fee_model_status="missing_or_unreviewed",
    )

    row = report["pairs"][0]
    assert row["enrichment"]["fees_available"] is False
    assert row["enrichment"]["fee_blocker_reason"] == "polymarket_fee_model_missing_or_unreviewed"
    assert row["enrichment"]["polymarket_fee_source_used"] == "missing_or_unreviewed"
    assert "missing_fees" in row["remaining_research_blockers"]
    assert "relationship_manual_review_required" in row["remaining_research_blockers"]


def test_enrich_live_match_candidates_informal_fee_status_does_not_unlock_fees(tmp_path: Path) -> None:
    _write_enrichment_inputs(tmp_path)

    report = build_live_match_candidate_enrichment_report(
        match_report_path=tmp_path / "live_readonly_match_report.json",
        snapshot_dir=tmp_path,
        timeout_seconds=1.0,
        max_snapshot_age_hours=24.0,
        kalshi_client=_FakeKalshiOrderbookClient(),
        polymarket_client=_FakePolymarketOrderbookClient(),
        polymarket_fee_model_status="reviewed_but_not_allowlisted",
    )

    row = report["pairs"][0]
    assert row["enrichment"]["fees_available"] is False
    assert "polymarket_fee_model_missing_or_unreviewed" in row["enrichment"]["fee_blocker_reason"]
    assert "missing_fees" in row["remaining_research_blockers"]


def test_enrich_live_match_candidates_unknown_polymarket_category_uses_conservative_unknown(tmp_path: Path) -> None:
    _write_enrichment_inputs(tmp_path)
    snapshot_payload = json.loads((tmp_path / "polymarket_live_readonly_snapshot.json").read_text(encoding="utf-8"))
    snapshot_payload["normalized_markets"][0]["market_id"] = "poly-unknown-1"
    snapshot_payload["normalized_markets"][0]["condition_id"] = "poly-unknown-1"
    snapshot_payload["normalized_markets"][0]["question"] = "Will qzxq happen?"
    (tmp_path / "polymarket_live_readonly_snapshot.json").write_text(json.dumps(snapshot_payload), encoding="utf-8")
    match_payload = json.loads((tmp_path / "live_readonly_match_report.json").read_text(encoding="utf-8"))
    match_payload["pairs"][0]["polymarket"]["market_id"] = "poly-unknown-1"
    match_payload["pairs"][0]["polymarket"]["question"] = "Will qzxq happen?"
    (tmp_path / "live_readonly_match_report.json").write_text(json.dumps(match_payload), encoding="utf-8")

    report = build_live_match_candidate_enrichment_report(
        match_report_path=tmp_path / "live_readonly_match_report.json",
        snapshot_dir=tmp_path,
        timeout_seconds=1.0,
        max_snapshot_age_hours=24.0,
        kalshi_client=_FakeKalshiOrderbookClient(),
        polymarket_client=_FakePolymarketOrderbookClient(),
    )

    row = report["pairs"][0]
    assert row["enrichment"]["fees_available"] is True
    assert row["enrichment"]["polymarket_fee_source_used"] == "conservative_unknown"
    assert row["enrichment"]["polymarket_fee_category"] == "other_general"
    assert row["enrichment"]["polymarket_fee_rate_used"] == 0.05
    assert "missing_fees" in row["resolved_research_blockers"]


def test_enrich_live_match_candidates_reviewed_fee_models_remove_only_fee_blocker(tmp_path: Path) -> None:
    _write_enrichment_inputs(tmp_path)

    report = build_live_match_candidate_enrichment_report(
        match_report_path=tmp_path / "live_readonly_match_report.json",
        snapshot_dir=tmp_path,
        timeout_seconds=1.0,
        max_snapshot_age_hours=24.0,
        kalshi_client=_FakeKalshiOrderbookClient(),
        polymarket_client=_FakePolymarketOrderbookClient(),
        polymarket_fee_model=FlatFeeModel(0.001),
        polymarket_fee_model_status="reviewed_conservative",
    )
    serialized = json.dumps(report)

    row = report["pairs"][0]
    assert row["action"] == "MANUAL_REVIEW"
    assert row["enrichment"]["fees_available"] is True
    assert row["enrichment"]["kalshi_estimated_fee_cents"] is not None
    assert row["enrichment"]["polymarket_estimated_fee_cents"] == 0.1
    assert row["enrichment"]["estimated_total_fees_cents"] is not None
    assert row["enrichment"]["fee_adjusted_gap_cents"] is not None
    assert "missing_fees" in row["resolved_research_blockers"]
    assert "missing_fees" not in row["remaining_research_blockers"]
    assert "relationship_manual_review_required" in row["remaining_research_blockers"]
    assert "sports_competition_scope_mismatch" in row["remaining_research_blockers"]
    assert report["summary"]["fees_available_count"] == 1
    assert report["summary"]["fee_adjusted_gap_cents"]["count"] == 1
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_enrich_live_match_candidates_missing_orderbooks_keep_blockers(tmp_path: Path) -> None:
    _write_enrichment_inputs(tmp_path)

    report = build_live_match_candidate_enrichment_report(
        match_report_path=tmp_path / "live_readonly_match_report.json",
        snapshot_dir=tmp_path,
        timeout_seconds=1.0,
        max_snapshot_age_hours=24.0,
        kalshi_client=_EmptyKalshiOrderbookClient(),
        polymarket_client=_EmptyPolymarketOrderbookClient(),
    )

    row = report["pairs"][0]
    assert row["enrichment"]["depth_available"] is False
    assert row["enrichment"]["quote_timestamp_available"] is False
    assert row["enrichment"]["fees_available"] is False
    assert "missing_orderbook_depth" in row["remaining_research_blockers"]
    assert "missing_quote_timestamp" in row["remaining_research_blockers"]
    assert "sports_competition_scope_mismatch" in row["remaining_research_blockers"]


def test_enrich_live_match_candidates_cli_is_explicit_and_writes_reports(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_enrichment_inputs(tmp_path)
    monkeypatch.setattr("scan.KalshiOrderbookClient", lambda **kwargs: _FakeKalshiOrderbookClient())
    monkeypatch.setattr("scan.PolymarketOrderbookClient", lambda **kwargs: _FakePolymarketOrderbookClient())

    result = main(
        [
            "enrich-live-match-candidates",
            "--match-report",
            str(tmp_path / "live_readonly_match_report.json"),
            "--snapshot-dir",
            str(tmp_path),
            "--json-output",
            str(tmp_path / "enrichment.json"),
            "--markdown-output",
            str(tmp_path / "enrichment.md"),
        ]
    )

    output = capsys.readouterr().out
    report_text = (tmp_path / "enrichment.json").read_text(encoding="utf-8") + (tmp_path / "enrichment.md").read_text(encoding="utf-8") + output
    assert result == 0
    assert "live_match_candidate_enrichment_status=OK" in output
    assert "data_source_mode=STATIC_FIXTURE" not in output
    assert "PAPER_CANDIDATE" not in report_text
    assert "POSSIBLE_ARB" not in report_text


def test_inspect_live_snapshots_works_with_sample_saved_files(tmp_path: Path, capsys) -> None:
    _write_sample_live_snapshots(tmp_path)
    json_output = tmp_path / "inspection.json"
    markdown_output = tmp_path / "inspection.md"

    result = inspect_live_snapshots(
        snapshot_dir=tmp_path,
        json_output=json_output,
        markdown_output=markdown_output,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "live_snapshot_inspection_status=OK" in output
    assert "PAPER_CANDIDATE" not in output
    assert "POSSIBLE_ARB" not in output
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    rows = {row["source_id"]: row for row in payload["rows"]}
    assert rows["kalshi"]["snapshot_contract"] == "normalized_markets_v1_like"
    assert rows["polymarket"]["match_shape_ready"] is True
    assert rows["polymarket"]["match_ready"] is True
    assert rows["polymarket"]["paper_simulation_ready"] is False
    assert rows["polymarket"]["can_participate_in_candidate_pair"] is True
    assert rows["polymarket"]["can_create_paper_candidate"] is False
    assert rows["the_odds_api"]["source_type"] == "REFERENCE_ONLY"
    assert rows["the_odds_api"]["can_create_paper_candidate"] is False
    assert rows["the_odds_api"]["reference_fields_present"]["sportsbook"] is True
    assert rows["the_odds_api"]["reference_fields_present"]["odds"] is True
    report_text = json_output.read_text(encoding="utf-8") + markdown_output.read_text(encoding="utf-8")
    assert "PAPER_CANDIDATE" not in report_text
    assert "POSSIBLE_ARB" not in report_text


def test_inspect_live_snapshots_missing_files_are_not_found_rows(tmp_path: Path) -> None:
    report = build_live_snapshot_inspection_report(snapshot_dir=tmp_path)

    assert all(row["safety_status"] == "NOT_FOUND" for row in report["rows"])
    assert all(row["missing_required_fields_for_future_matching"] == ["snapshot_file"] for row in report["rows"])


def test_inspect_live_snapshots_does_not_print_secretish_values(tmp_path: Path, capsys) -> None:
    _write_sample_live_snapshots(tmp_path)
    secret = "super-secret-inspection-token"
    (tmp_path / "kalshi_live_readonly_snapshot.json").write_text(
        json.dumps(_sample_executable_snapshot("kalshi", raw_marker=secret)),
        encoding="utf-8",
    )

    result = inspect_live_snapshots(
        snapshot_dir=tmp_path,
        json_output=tmp_path / "inspection.json",
        markdown_output=tmp_path / "inspection.md",
    )

    output = capsys.readouterr().out
    assert result == 0
    assert secret not in output


def test_match_live_readonly_snapshots_missing_files_refuses_cleanly(tmp_path: Path, capsys) -> None:
    result = match_live_readonly_snapshots(
        snapshot_dir=tmp_path,
        min_similarity=0.68,
        max_snapshot_age_hours=24.0,
        json_output=tmp_path / "match.json",
        markdown_output=tmp_path / "match.md",
        include_reference_context=True,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "live_readonly_match_status=VALIDATION_FAILED" in output
    payload = json.loads((tmp_path / "match.json").read_text(encoding="utf-8"))
    assert payload["pairs"] == []
    assert any("snapshot_not_found" in issue for issue in payload["validation_errors"])


def test_match_live_readonly_snapshots_refuses_reference_snapshot_as_executable_leg(tmp_path: Path) -> None:
    _write_sample_live_snapshots(tmp_path)
    reference_payload = _sample_reference_snapshot()
    (tmp_path / "kalshi_live_readonly_snapshot.json").write_text(json.dumps(reference_payload), encoding="utf-8")

    report = build_live_readonly_match_report(snapshot_dir=tmp_path)

    assert report["status"] == "VALIDATION_FAILED"
    assert any("kalshi:source_id_invalid" == issue for issue in report["validation_errors"])
    assert any("kalshi:source_type_not_executable" == issue for issue in report["validation_errors"])
    assert report["pairs"] == []


def test_match_live_readonly_snapshots_outputs_research_only_actions(tmp_path: Path) -> None:
    _write_sample_live_snapshots(tmp_path)

    report = build_live_readonly_match_report(snapshot_dir=tmp_path, include_reference_context=True)
    serialized = json.dumps(report)

    assert report["status"] == "OK"
    assert report["research_only"] is True
    assert report["readiness_promotion"] == "none"
    assert report["reference_context_role"] == "reference_context_only"
    assert report["reference_context_used"] is True
    assert report["match_summary"]["pair_count"] == 1
    assert set(report["match_summary"]["actions"]) <= {"WATCH", "MANUAL_REVIEW"}
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized
    assert "missing_orderbook_depth" in serialized
    assert "missing_fees" in serialized


def test_match_live_readonly_snapshots_does_not_print_secretish_values(tmp_path: Path, capsys) -> None:
    _write_sample_live_snapshots(tmp_path)
    secret = "super-secret-match-token"
    kalshi = _sample_executable_snapshot("kalshi")
    kalshi["api_key"] = secret
    (tmp_path / "kalshi_live_readonly_snapshot.json").write_text(json.dumps(kalshi), encoding="utf-8")

    result = match_live_readonly_snapshots(
        snapshot_dir=tmp_path,
        min_similarity=0.68,
        max_snapshot_age_hours=24.0,
        json_output=tmp_path / "match.json",
        markdown_output=tmp_path / "match.md",
        include_reference_context=True,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert secret not in output
    payload_text = (tmp_path / "match.json").read_text(encoding="utf-8")
    assert secret not in payload_text


def test_diagnose_live_matching_zero_pairs_still_reports_top_rejections(tmp_path: Path, capsys) -> None:
    _write_disjoint_live_snapshots(tmp_path)

    result = diagnose_live_matching(
        snapshot_dir=tmp_path,
        min_similarity=0.68,
        top_limit=5,
        json_output=tmp_path / "diagnostics.json",
        markdown_output=tmp_path / "diagnostics.md",
    )

    output = capsys.readouterr().out
    payload = json.loads((tmp_path / "diagnostics.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload) + (tmp_path / "diagnostics.md").read_text(encoding="utf-8") + output
    assert result == 0
    assert "live_matching_diagnostics_status=OK" in output
    assert payload["comparison_summary"]["raw_cross_source_candidate_comparisons"] == 1
    assert payload["comparison_summary"]["rejected_by_low_title_text_similarity"] == 1
    assert payload["top_rejected_pairs"]
    assert payload["top_rejected_pairs"][0]["relationship_status"] == "REJECTED_DIAGNOSTIC"
    assert payload["reference_context"]["role"] == "reference_context_only"
    assert "PAPER" not in serialized
    assert "PAPER_CANDIDATE" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_diagnose_live_matching_cli_uses_conservative_threshold(tmp_path: Path, capsys) -> None:
    _write_disjoint_live_snapshots(tmp_path)

    result = main(
        [
            "diagnose-live-matching",
            "--snapshot-dir",
            str(tmp_path),
            "--json-output",
            str(tmp_path / "diagnostics.json"),
            "--markdown-output",
            str(tmp_path / "diagnostics.md"),
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads((tmp_path / "diagnostics.json").read_text(encoding="utf-8"))
    assert result == 0
    assert "live_matching_diagnostics_status=OK" in output
    assert payload["matching_thresholds"]["min_similarity"] == 0.68
    assert payload["matching_thresholds"]["thresholds_changed_by_diagnostics"] is False


def test_diagnose_live_matching_missing_snapshots_refuses_cleanly(tmp_path: Path, capsys) -> None:
    result = diagnose_live_matching(
        snapshot_dir=tmp_path,
        min_similarity=0.68,
        top_limit=5,
        json_output=tmp_path / "diagnostics.json",
        markdown_output=tmp_path / "diagnostics.md",
    )

    output = capsys.readouterr().out
    payload = json.loads((tmp_path / "diagnostics.json").read_text(encoding="utf-8"))
    assert result == 1
    assert "live_matching_diagnostics_status=VALIDATION_FAILED" in output
    assert payload["comparison_summary"]["raw_cross_source_candidate_comparisons"] == 0
    assert payload["comparison_summary"]["rejected_by_source_schema_validation"] > 0
    assert payload["top_rejected_pairs"] == []


def test_diagnose_live_matching_uses_reference_context_only(tmp_path: Path) -> None:
    _write_disjoint_live_snapshots(tmp_path)
    report = build_live_matching_diagnostics_report(snapshot_dir=tmp_path, min_similarity=0.68)

    assert report["reference_context"]["source_id"] == "the_odds_api"
    assert report["reference_context"]["role"] == "reference_context_only"
    assert report["reference_context"]["record_count"] == 1
    assert report["safety"]["uses_reference_as_executable_leg"] is False


def test_diagnose_live_matching_default_scan_remains_static_fixture(tmp_path: Path) -> None:
    _write_disjoint_live_snapshots(tmp_path)
    report = build_live_matching_diagnostics_report(snapshot_dir=tmp_path)

    assert report["default_scan_data_source_mode"] == "STATIC_FIXTURE"
    assert report["live_api_fetch_attempted"] is False
    assert report["matching_thresholds"]["thresholds_changed_by_diagnostics"] is False


def test_diagnose_live_matching_does_not_print_secretish_values(tmp_path: Path, capsys) -> None:
    _write_disjoint_live_snapshots(tmp_path)
    secret = "super-secret-diagnostics-token"
    kalshi = _sample_live_market_snapshot(
        "kalshi",
        question="Will the Thunder win?",
        event_title="Thunder vs Wolves",
        raw={"marker": secret},
    )
    (tmp_path / "kalshi_live_readonly_snapshot.json").write_text(json.dumps(kalshi), encoding="utf-8")

    result = diagnose_live_matching(
        snapshot_dir=tmp_path,
        min_similarity=0.68,
        top_limit=5,
        json_output=tmp_path / "diagnostics.json",
        markdown_output=tmp_path / "diagnostics.md",
    )

    output = capsys.readouterr().out
    report_text = (tmp_path / "diagnostics.json").read_text(encoding="utf-8") + (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert result == 0
    assert secret not in output
    assert secret not in report_text


def test_non_sports_near_miss_diagnostics_with_fixture_data(tmp_path: Path) -> None:
    sweep_path = _write_non_sports_near_miss_fixture(tmp_path)

    report = build_non_sports_near_miss_diagnostics_report(
        sweep_report=sweep_path,
        min_similarity=0.68,
        top_limit=4,
    )

    assert report["status"] == "OK"
    assert report["default_scan_data_source_mode"] == "STATIC_FIXTURE"
    assert report["matching_thresholds"]["thresholds_changed_by_diagnostics"] is False
    assert report["safety"]["same_payoff_asserted"] is False
    assert report["summary"]["near_miss_count"] >= 2
    assert any(row["category"] == "ai" and row["query"] == "OpenAI" for row in report["near_misses"])
    assert all(row["diagnostic_only"] is True for row in report["near_misses"])
    assert all(row["same_payoff_asserted"] is False for row in report["near_misses"])
    assert any("threshold_mismatch" in row["blocker_labels"] for row in report["near_misses"])
    assert any("entity_mismatch" in row["blocker_labels"] for row in report["near_misses"])
    assert "PAPER_CANDIDATE" not in json.dumps(report)
    assert "POSSIBLE_ARB" not in json.dumps(report)


def test_non_sports_near_miss_command_writes_reports_and_redacts_secrets(tmp_path: Path, capsys) -> None:
    sweep_path = _write_non_sports_near_miss_fixture(tmp_path, raw_secret="super-secret-near-miss-token")

    result = diagnose_non_sports_near_misses(
        sweep_report=sweep_path,
        min_similarity=0.68,
        top_limit=4,
        json_output=tmp_path / "near_miss.json",
        markdown_output=tmp_path / "near_miss.md",
    )

    output = capsys.readouterr().out
    report_text = (tmp_path / "near_miss.json").read_text(encoding="utf-8") + (tmp_path / "near_miss.md").read_text(encoding="utf-8") + output
    payload = json.loads((tmp_path / "near_miss.json").read_text(encoding="utf-8"))
    assert result == 0
    assert "non_sports_near_miss_diagnostics_status=OK" in output
    assert payload["live_api_fetch_attempted"] is False
    assert "super-secret-near-miss-token" not in report_text
    assert "PAPER_CANDIDATE" not in report_text
    assert "POSSIBLE_ARB" not in report_text


def test_diagnostic_entities_use_word_boundaries_for_short_tokens() -> None:
    assert "ethereum" not in _diagnostic_entities("Will Netherlands win the 2026 FIFA World Cup?")
    assert "ethereum" in _diagnostic_entities("Will ETH be above 5000 dollars?")
    assert "ethereum" in _diagnostic_entities("Will Ethereum be above 5000 dollars?")
    assert "bitcoin" in _diagnostic_entities("Will BTC hit 100k?")
    assert "openai" in _diagnostic_entities("Will OpenAI release GPT-5?")
    assert "openai" in _diagnostic_entities("Will GPT5 launch?")
    assert "fed" in _diagnostic_entities("Will the FOMC cut rates?")
    assert "fed" in _diagnostic_entities("Will the federal funds rate fall?")
    assert "cpi" in _diagnostic_entities("Will CPI be above 3%?")


def test_eth_fifa_near_miss_recommends_source_targeting(tmp_path: Path) -> None:
    sweep_path = _write_non_sports_near_miss_fixture(tmp_path)

    report = build_non_sports_near_miss_diagnostics_report(
        sweep_report=sweep_path,
        min_similarity=0.68,
        top_limit=4,
    )

    row = next(row for row in report["near_misses"] if row["category"] == "crypto" and row["query"] == "Ethereum")
    assert "ethereum" not in row["polymarket"]["entities_detected"]
    assert row["kalshi"]["entities_detected"] == ["ethereum"]
    assert "vague_event_wording" in row["blocker_labels"]
    assert row["recommended_next_step"] == "better_source_targeting"


def _write_non_sports_near_miss_fixture(path: Path, raw_secret: str = "") -> Path:
    rows = []
    fixtures = [
        (
            "ai",
            "OpenAI",
            "Will OpenAI release GPT-5 by the end of 2026?",
            "OpenAI GPT-5 release",
            "Will OpenAI release a new model in 2026?",
            "OpenAI product launch",
        ),
        (
            "crypto",
            "Ethereum",
            "Will Ethereum be above 5000 dollars by end of 2026?",
            "Ethereum price",
            "Will Netherlands win the 2026 FIFA World Cup?",
            "World Cup",
        ),
        (
            "macro",
            "Fed",
            "Will the Fed cut rates at the June 2026 meeting?",
            "Federal Reserve rates",
            "Will CPI inflation be above 3% in 2026?",
            "CPI inflation",
        ),
    ]
    for category, query, kalshi_question, kalshi_event, poly_question, poly_event in fixtures:
        snapshot_dir = path / f"overlap_{category}_{query.lower()}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        raw = {"marker": raw_secret} if raw_secret else None
        (snapshot_dir / "kalshi_live_readonly_snapshot.json").write_text(
            json.dumps(
                _sample_live_market_snapshot(
                    "kalshi",
                    question=kalshi_question,
                    event_title=kalshi_event,
                    raw=raw,
                )
            ),
            encoding="utf-8",
        )
        (snapshot_dir / "polymarket_live_readonly_snapshot.json").write_text(
            json.dumps(
                _sample_live_market_snapshot(
                    "polymarket",
                    question=poly_question,
                    event_title=poly_event,
                    raw=raw,
                )
            ),
            encoding="utf-8",
        )
        rows.append(
            {
                "category": category,
                "query": query,
                "snapshot_dir": str(snapshot_dir),
                "kalshi_retained_count": 1,
                "polymarket_retained_count": 1,
                "pair_count": 0,
            }
        )
    sweep_path = path / "live_overlap_sweep.json"
    sweep_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "live_overlap_sweep",
                "status": "OK",
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )
    return sweep_path


def _write_sample_live_snapshots(path: Path) -> None:
    (path / "kalshi_live_readonly_snapshot.json").write_text(
        json.dumps(_sample_executable_snapshot("kalshi")),
        encoding="utf-8",
    )
    (path / "polymarket_live_readonly_snapshot.json").write_text(
        json.dumps(_sample_executable_snapshot("polymarket")),
        encoding="utf-8",
    )
    (path / "the_odds_api_reference_snapshot.json").write_text(
        json.dumps(_sample_reference_snapshot()),
        encoding="utf-8",
    )


def _write_disjoint_live_snapshots(path: Path) -> None:
    (path / "kalshi_live_readonly_snapshot.json").write_text(
        json.dumps(
            _sample_live_market_snapshot(
                "kalshi",
                question="Will the Thunder win the basketball game?",
                event_title="Thunder vs Wolves",
                close_time="2026-05-22T22:00:00+00:00",
            )
        ),
        encoding="utf-8",
    )
    (path / "polymarket_live_readonly_snapshot.json").write_text(
        json.dumps(
            _sample_live_market_snapshot(
                "polymarket",
                question="Will Bitcoin be above 120000 dollars?",
                event_title="Bitcoin price",
                end_date="2026-05-22T22:00:00+00:00",
            )
        ),
        encoding="utf-8",
    )
    (path / "the_odds_api_reference_snapshot.json").write_text(
        json.dumps(_sample_reference_snapshot()),
        encoding="utf-8",
    )


def _write_enrichment_inputs(path: Path) -> None:
    kalshi = _sample_executable_snapshot("kalshi")
    kalshi["normalized_markets"].append(
        {
            **_sample_executable_snapshot("kalshi")["normalized_markets"][0],
            "market_id": "kalshi-extra",
            "ticker": "KXEXTRA",
            "question": "Will an unrelated Kalshi event happen?",
        }
    )
    kalshi["normalized_markets"][0]["market_id"] = "KXNBA-1"
    kalshi["normalized_markets"][0]["ticker"] = "KXNBA-1"
    kalshi["normalized_markets"][0]["question"] = "Will Boston win the basketball championship?"
    polymarket = _sample_executable_snapshot("polymarket")
    polymarket["normalized_markets"][0]["market_id"] = "poly-nba-1"
    polymarket["normalized_markets"][0]["condition_id"] = "poly-nba-1"
    polymarket["normalized_markets"][0]["question"] = "Will Boston win the NBA Finals?"
    polymarket["normalized_markets"][0]["outcomes"] = [{"name": "Yes"}, {"name": "No"}]
    polymarket["normalized_markets"][0]["raw"] = {
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["yes-token-1","no-token-1"]',
    }
    polymarket["normalized_markets"].append(
        {
            **polymarket["normalized_markets"][0],
            "market_id": "poly-extra",
            "condition_id": "poly-extra",
            "question": "Will an unrelated Polymarket event happen?",
            "raw": {"outcomes": '["Yes","No"]', "clobTokenIds": '["yes-token-extra","no-token-extra"]'},
        }
    )
    (path / "kalshi_live_readonly_snapshot.json").write_text(json.dumps(kalshi), encoding="utf-8")
    (path / "polymarket_live_readonly_snapshot.json").write_text(json.dumps(polymarket), encoding="utf-8")
    (path / "live_readonly_match_report.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "live_readonly_saved_snapshot_match",
                "pairs": [
                    {
                        "action": "MANUAL_REVIEW",
                        "polymarket": {"market_id": "poly-nba-1", "question": "Will Boston win the NBA Finals?"},
                        "kalshi": {"ticker": "KXNBA-1", "question": "Will Boston win the basketball championship?"},
                        "research_blockers": [
                            "missing_orderbook_depth",
                            "missing_fees",
                            "missing_quote_timestamp",
                            "relationship_manual_review_required",
                            "sports_competition_scope_mismatch",
                        ],
                        "contract_relationship": {
                            "relationship": "NEAR_EQUIVALENT",
                            "same_payoff": False,
                            "manual_review_required": True,
                            "blocking_reasons": ["sports_competition_scope_mismatch"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _sample_live_market_snapshot(
    source_id: str,
    *,
    question: str,
    event_title: str,
    end_date: str = "2026-05-22T22:00:00+00:00",
    close_time: str = "2026-05-22T22:00:00+00:00",
    raw: dict | None = None,
) -> dict:
    venue = "kalshi" if source_id == "kalshi" else "polymarket"
    row = {
        "venue": venue,
        "market_id": f"{venue}-market-1",
        "event_title": event_title,
        "question": question,
        "end_date": end_date,
        "best_bid": 0.45,
        "best_ask": 0.55,
        "active": True,
        "closed": False,
        "liquidity": 1000,
        "raw": raw or {},
    }
    if source_id == "kalshi":
        row["ticker"] = "KXSAMPLE"
        row["close_time"] = close_time
    else:
        row["condition_id"] = "0xsample"
    return {
        "schema_version": 1,
        "source_id": source_id,
        "source": f"{source_id}_snapshot",
        "data_source_mode": "LIVE_API",
        "captured_at": "2026-05-22T19:00:00+00:00",
        "live_fetch_succeeded": True,
        "event_count": 1,
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [row],
    }


def _sample_executable_snapshot(source_id: str, raw_marker: str = "") -> dict:
    venue = "kalshi" if source_id == "kalshi" else "polymarket"
    row = {
        "venue": venue,
        "market_id": f"{venue}-market-1",
        "event_title": "Sample event",
        "question": "Will sample happen?",
        "end_date": "2026-05-22T20:00:00+00:00",
        "best_bid": 0.45,
        "best_ask": 0.55,
        "raw": {"marker": raw_marker} if raw_marker else {},
    }
    if source_id == "kalshi":
        row["ticker"] = "KXSAMPLE"
        row["close_time"] = "2026-05-22T20:00:00+00:00"
    else:
        row["condition_id"] = "0xsample"
    return {
        "schema_version": 1,
        "source_id": source_id,
        "source": f"{source_id}_snapshot",
        "data_source_mode": "LIVE_API",
        "captured_at": "2026-05-22T19:00:00+00:00",
        "live_fetch_succeeded": True,
        "event_count": 1,
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [row],
    }


def _sample_reference_snapshot() -> dict:
    return {
        "schema_version": 1,
        "schema_kind": "reference_snapshot_v1",
        "source_id": "the_odds_api",
        "source_type": "REFERENCE_ONLY",
        "permission": "REFERENCE_ONLY",
        "data_source_mode": "LIVE_API",
        "retrieved_at": "2026-05-22T19:00:00+00:00",
        "stale_after": "2026-05-22T19:15:00+00:00",
        "live_fetch_succeeded": True,
        "event_count": 1,
        "normalized_count": 1,
        "normalized_records": [
            {
                "source_id": "the_odds_api",
                "source_type": "REFERENCE_ONLY",
                "permission": "REFERENCE_ONLY",
                "event_id": "event-1",
                "event_title": "Away at Home",
                "commence_time": "2026-05-22T20:00:00+00:00",
                "bookmaker": "Example Book",
                "bookmaker_key": "example",
                "market_type": "h2h",
                "outcome_name": "Home",
                "american_odds": -110,
                "implied_probability": 0.52381,
                "no_vig_probability": 0.5,
                "retrieved_at": "2026-05-22T19:00:00+00:00",
                "stale_after": "2026-05-22T19:15:00+00:00",
                "is_executable": False,
                "usable_for_trade_decision": False,
            }
        ],
    }


class _FakeOddsClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_reference_snapshot(self, **kwargs) -> dict:
        return {
            "schema_version": 1,
            "schema_kind": "reference_snapshot_v1",
            "source_id": "the_odds_api",
            "source_type": "REFERENCE_ONLY",
            "permission": "REFERENCE_ONLY",
            "normalized_count": 2,
            "normalized_records": [],
        }


def _patch_public_clients(monkeypatch) -> None:
    monkeypatch.setattr("scan.KalshiReadOnlyClient", _FakeKalshiClient)
    monkeypatch.setattr("scan.PolymarketGammaClient", _FakePolymarketClient)


class _FakeKalshiClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        return {
            "schema_version": 1,
            "source": "kalshi_markets",
            "captured_at": "2026-05-22T00:00:00+00:00",
            "normalized_count": 1,
            "normalized_markets": [],
        }


class _FakePolymarketClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        return {
            "schema_version": 1,
            "source": "polymarket_gamma",
            "captured_at": "2026-05-22T00:00:00+00:00",
            "normalized_count": 1,
            "normalized_markets": [],
        }


class _InventoryKalshiClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_series_inventory(self, **kwargs) -> dict:
        return {
            "series": [
                {"series_ticker": "KXFED", "title": "Federal Reserve rates", "category": "Economics", "active_market_count": 12},
                {"series_ticker": "KXCPI", "title": "CPI inflation", "category": "Economics", "active_market_count": 8},
                {"series_ticker": "KXETH", "title": "Ethereum price", "category": "Crypto", "active_market_count": 9},
                {"series_ticker": "KXBTC", "title": "Bitcoin price", "category": "Crypto", "active_market_count": 5},
                {"series_ticker": "KXTSLA", "title": "Tesla production", "category": "Companies", "active_market_count": 4},
            ]
        }


class _InventoryPolymarketClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_tag_inventory(self, **kwargs) -> dict:
        return {
            "tags": [
                {"id": 1, "slug": "politics", "label": "Politics", "active_market_count": 20},
                {"id": 2, "slug": "crypto", "label": "Crypto", "active_market_count": 30},
                {"id": 3, "slug": "business", "label": "Business and companies", "active_market_count": 10},
                {"id": 4, "slug": "ai", "label": "AI and OpenAI", "active_market_count": 15},
            ]
        }


class _PolymarketTagsWithBadFallbackLabelClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_tag_inventory(self, **kwargs) -> dict:
        return {
            "tags": [
                {"id": 1, "slug": "crypto", "label": "Crypto", "active_market_count": 30},
                {"id": 2, "label": "Open AI", "active_market_count": 20},
                {"id": 3, "label": "Bad Label !!!", "active_market_count": 99},
            ]
        }


class _HundredTagInventoryPolymarketClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_tag_inventory(self, **kwargs) -> dict:
        return {
            "tags": [
                {"id": index, "slug": f"tag-{index}", "label": f"Tag {index}", "active_market_count": index}
                for index in range(100)
            ]
        }


class _FailingInventoryKalshiClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_series_inventory(self, **kwargs) -> dict:
        raise RuntimeError("Kalshi series API returned HTTP 503 for /series")


class _FailingInventoryPolymarketClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_tag_inventory(self, **kwargs) -> dict:
        raise RuntimeError("Polymarket Gamma API returned HTTP 503 for /tags")


class _SecretInventoryKalshiClient(_InventoryKalshiClient):
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def fetch_series_inventory(self, **kwargs) -> dict:
        payload = super().fetch_series_inventory(**kwargs)
        payload["token"] = self.secret
        return payload


class _FailingKalshiClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        raise RuntimeError("Kalshi markets API request failed: test failure")


class _OverlapKalshiClient:
    last_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        type(self).last_kwargs = kwargs
        return _overlap_snapshot(
            "kalshi",
            [
                _overlap_market("kalshi", "KXNBA-1", "Will the Boston Celtics win this NBA game?", "Boston Celtics vs Knicks"),
                _overlap_market("kalshi", "KXPOL-1", "Will a Senate bill pass?", "Senate politics"),
            ],
        )


class _OverlapPolymarketClient:
    last_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        type(self).last_kwargs = kwargs
        return _overlap_snapshot(
            "polymarket",
            [
                _overlap_market("polymarket", "poly-nba-1", "Will Boston win the NBA game?", "Boston Celtics vs Knicks"),
                _overlap_market("polymarket", "poly-openai-1", "Will OpenAI release a model?", "OpenAI"),
            ],
        )


class _UnrelatedKalshiClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        return _overlap_snapshot(
            "kalshi",
            [_overlap_market("kalshi", "KXWEATHER-1", "Will it rain in Chicago?", "Chicago weather")],
        )


class _LeagueOverlapKalshiClient:
    last_kwargs: dict = {}
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        type(self).last_kwargs = kwargs
        type(self).calls.append(kwargs)
        if kwargs.get("series_ticker") == "KXNBA":
            markets = [
                _overlap_market("kalshi", "KXNBA-1", "Will the Boston Celtics win this NBA game?", "Boston Celtics vs Knicks")
            ]
        elif kwargs.get("series_ticker") == "KXMLB":
            markets = [
                _overlap_market("kalshi", "KXMLB-1", "Will the New York Yankees win this MLB game?", "Yankees vs Red Sox")
            ]
        elif kwargs.get("series_ticker") == "KXFED":
            markets = [
                _overlap_market("kalshi", "KXFED-1", "Will the Fed cut interest rates in 2026?", "Federal Reserve rates")
            ]
        elif kwargs.get("series_ticker") in {"FEDDECISION", "KXFOMCDISSENTCOUNT", "KXFOMCVOTE"}:
            markets = []
        elif kwargs.get("series_ticker") == "AIDEBATES":
            markets = [
                _overlap_market("kalshi", "AIDEBATES-1", "Will OpenAI release GPT-6 in 2026?", "OpenAI AI model")
            ]
        elif kwargs.get("series_ticker") in {"AILEGISLATION", "AITURING", "APPLEAI", "GPT45", "KXGPT5"}:
            markets = []
        elif kwargs.get("series_ticker") in {"KXCPI", "CPI", "CPIYOY", "CPICORE", "KXCOREUND"}:
            markets = []
        else:
            markets = [
                _overlap_market("kalshi", "KXNBA-1", "Will the Boston Celtics win this NBA game?", "Boston Celtics vs Knicks"),
                _overlap_market("kalshi", "KXMLB-1", "Will the New York Yankees win this MLB game?", "Yankees vs Red Sox"),
                _overlap_market("kalshi", "KXNFL-1", "Will the Eagles win this NFL game?", "Eagles vs Giants"),
            ]
        return _overlap_snapshot(
            "kalshi",
            markets,
        )


class _LeagueOverlapPolymarketClient:
    last_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        type(self).last_kwargs = kwargs
        if kwargs.get("tag_slug") == "nba":
            markets = [_overlap_market("polymarket", "poly-nba-1", "Will Boston win the NBA game?", "Boston Celtics vs Knicks")]
        elif kwargs.get("tag_slug") == "mlb":
            markets = [_overlap_market("polymarket", "poly-mlb-1", "Will the Yankees win this MLB game?", "Yankees vs Red Sox")]
        elif kwargs.get("tag_slug") == "openai":
            markets = [_overlap_market("polymarket", "poly-ai-1", "Will OpenAI release GPT-6 in 2026?", "OpenAI AI model")]
        else:
            markets = [
                _overlap_market("polymarket", "poly-nba-1", "Will Boston win the NBA game?", "Boston Celtics vs Knicks"),
                _overlap_market("polymarket", "poly-mlb-1", "Will the Yankees win this MLB game?", "Yankees vs Red Sox"),
                _overlap_market("polymarket", "poly-nhl-1", "Will the Rangers win this NHL game?", "Rangers vs Bruins"),
            ]
        return _overlap_snapshot(
            "polymarket",
            markets,
        )


class _UnrelatedPolymarketClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        return _overlap_snapshot(
            "polymarket",
            [_overlap_market("polymarket", "poly-crypto-1", "Will Bitcoin reach 150000 dollars?", "Bitcoin price")],
        )


class _SecretOverlapKalshiClient:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def fetch_market_snapshot(self, **kwargs) -> dict:
        snapshot = _overlap_snapshot(
            "kalshi",
            [_overlap_market("kalshi", "KXNBA-1", "Will the Boston Celtics win this NBA game?", "Boston Celtics vs Knicks")],
        )
        snapshot["raw_response"] = {"api_key": self.secret}
        return snapshot


class _SweepKalshiClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        return _overlap_snapshot(
            "kalshi",
            [
                _overlap_market("kalshi", "KXFED-1", "Will the Fed cut interest rates in 2026?", "Federal Reserve rates"),
                _overlap_market("kalshi", "KXBTC-1", "Will Bitcoin be above 100000 dollars in 2026?", "Bitcoin price"),
            ],
        )


class _SweepPolymarketClient:
    def __init__(self, **kwargs) -> None:
        pass

    def fetch_market_snapshot(self, **kwargs) -> dict:
        return _overlap_snapshot(
            "polymarket",
            [
                _overlap_market("polymarket", "poly-fed-1", "Will the Fed cut interest rates in 2026?", "Federal Reserve rates"),
                _overlap_market("polymarket", "poly-btc-1", "Will Bitcoin hit 100000 dollars in 2026?", "Bitcoin price"),
            ],
        )


def _overlap_snapshot(source_id: str, markets: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "source": f"{source_id}_markets",
        "source_id": source_id,
        "data_source_mode": "LIVE_API",
        "captured_at": "2026-05-22T19:00:00+00:00",
        "live_fetch_succeeded": True,
        "event_count": len(markets),
        "market_count": len(markets),
        "normalized_count": len(markets),
        "normalized_markets": markets,
        "raw_response": {"markets": []},
    }


def _overlap_market(source_id: str, identifier: str, question: str, event_title: str) -> dict:
    row = {
        "venue": source_id,
        "market_id": identifier,
        "event_title": event_title,
        "question": question,
        "end_date": "2026-05-22T22:00:00+00:00",
        "best_bid": 0.45,
        "best_ask": 0.55,
        "active": True,
        "closed": False,
        "raw": {},
    }
    if source_id == "kalshi":
        row["ticker"] = identifier
        row["close_time"] = "2026-05-22T22:00:00+00:00"
    else:
        row["condition_id"] = identifier
    return row


class _FakeKalshiOrderbookClient:
    def __init__(self) -> None:
        self.tickers: list[str] = []

    def endpoint_for(self, ticker: str) -> str:
        return f"https://example.test/kalshi/{ticker}"

    def fetch_orderbook(self, ticker: str) -> dict:
        self.tickers.append(ticker)
        return {"orderbook": {"yes": [[0.40, 12]], "no": [[0.55, 10]]}}


class _FakePolymarketOrderbookClient:
    def __init__(self) -> None:
        self.token_ids: list[str] = []

    def endpoint_for(self, token_id: str) -> str:
        return f"https://example.test/polymarket/{token_id}"

    def fetch_orderbook(self, token_id: str) -> dict:
        self.token_ids.append(token_id)
        return {"bids": [{"price": "0.41", "size": "20"}], "asks": [{"price": "0.43", "size": "18"}]}


class _EmptyKalshiOrderbookClient(_FakeKalshiOrderbookClient):
    def fetch_orderbook(self, ticker: str) -> dict:
        self.tickers.append(ticker)
        return {"orderbook": {"yes": [], "no": []}}


class _EmptyPolymarketOrderbookClient(_FakePolymarketOrderbookClient):
    def fetch_orderbook(self, token_id: str) -> dict:
        self.token_ids.append(token_id)
        return {"bids": [], "asks": []}
