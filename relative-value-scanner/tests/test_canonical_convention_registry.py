from __future__ import annotations

import json
from datetime import datetime, timezone

import scan
from relative_value.canonical_convention_registry import (
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    FAMILY_FED_FOMC,
    REGISTRY_VERSION,
    build_canonical_convention_registry_audit,
    load_canonical_convention_registry,
    match_canonical_registry_entry,
)
from relative_value.settlement_evidence_burden import (
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_EXECUTION_EVALUATION_READY,
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
    build_settlement_evidence_burden_report,
)


def _registry_entry(**overrides):
    entry = {
        "registry_version": REGISTRY_VERSION,
        "entry_id": "fed-fomc-source-convention",
        "family": FAMILY_FED_FOMC,
        "reviewer": "manual_reviewer",
        "reviewed_at": "2026-05-25T00:00:00+00:00",
        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": "KXFED"},
        "typed_key_requirements": {
            "required": ["meeting_date", "rate_bound", "threshold_percent", "source_convention"],
            "match": {"source_convention": "federal_reserve_official_website"},
        },
        "canonical_source_kind": "official_source_url",
        "canonical_source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "evidence_quote_or_excerpt": {
            "kind": "official_source_excerpt",
            "text": "The Federal Reserve publishes FOMC calendars and policy decisions on its official website.",
        },
        "limitations": ["Manual registry convention only."],
        "review_until": "2026-12-31",
        "confidence": "high",
    }
    entry.update(overrides)
    return entry


def _write_registry(path, entries):
    path.write_text(
        json.dumps({"registry_version": REGISTRY_VERSION, "entries": entries}),
        encoding="utf-8",
    )


def _fed_typed(source_convention="federal_reserve_official_website"):
    return {
        "required": ["meeting_date", "rate_bound", "threshold_percent", "source_convention"],
        "present": ["meeting_date", "rate_bound", "threshold_percent", "source_convention"],
        "missing": [],
        "evidence": {
            "meeting_date": {"value": "Apr 28, 2027", "source": "rules_text:date_pattern"},
            "rate_bound": {"value": "upper_bound", "source": "rules_text:bound_phrase"},
            "threshold_percent": {"value": 4.25, "source": "ticker_T_pattern"},
            "source_convention": {"value": source_convention, "source": "rules_text:fed_source_phrase"},
        },
    }


def _btc_entry(**overrides):
    entry = _registry_entry(
        entry_id="btc-brti-exact-threshold",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        applies_to_scope={"venue": "kalshi", "event_ticker_prefix": "KXBTC"},
        typed_key_requirements={
            "required": [
                "asset",
                "threshold_value",
                "threshold_operator",
                "measurement_date",
                "price_source_index",
            ],
            "match": {
                "asset": "BTC",
                "threshold_value": 86249.99,
                "threshold_operator": "above",
                "measurement_date": "May 25, 2026",
                "price_source_index": "brti",
            },
        },
        canonical_source_kind="crypto_index_official",
        canonical_source_url="https://www.cfbenchmarks.com/data/indices/BRTI",
        evidence_quote_or_excerpt={
            "kind": "official_source_excerpt",
            "text": "CF Benchmarks publishes the Bitcoin Real Time Index.",
        },
    )
    entry.update(overrides)
    return entry


def _btc_scope_entry(**overrides):
    entry = _registry_entry(
        entry_id="btc-brti-source-scope",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        applies_to_scope={"venue": "kalshi", "event_ticker_prefix": "KXBTCD"},
        typed_key_requirements={
            "required": [
                "asset",
                "measurement_date",
                "price_source_index",
            ],
            "match": {
                "asset": "BTC",
                "measurement_date": "May 22, 2026",
                "price_source_index": "cf benchmarks",
            },
        },
        canonical_source_kind="crypto_index_official",
        canonical_source_url="https://www.example.com/reviewed-btcd-source",
        evidence_quote_or_excerpt={
            "kind": "saved_venue_rules_excerpt",
            "text": "Saved venue rules identify CF Benchmarks' Bitcoin Real-Time Index as the settlement source.",
        },
        limitations=["Manual source-convention scope only; row-level threshold typed keys must still match independently."],
        review_until="2026-08-23",
        confidence="medium",
    )
    entry.update(overrides)
    return entry


def _btc_typed(**values):
    defaults = {
        "asset": "BTC",
        "threshold_value": 86249.99,
        "threshold_operator": "above",
        "measurement_date": "May 25, 2026",
        "price_source_index": "brti",
    }
    defaults.update(values)
    return {
        "required": list(defaults),
        "present": list(defaults),
        "missing": [],
        "evidence": {key: {"value": value, "source": "fixture"} for key, value in defaults.items()},
    }


def _write_snapshot(path, *, markets):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "custom",
                "captured_at": "2026-05-25T12:00:00+00:00",
                "normalized_markets": markets,
            }
        ),
        encoding="utf-8",
    )


def test_valid_fed_registry_entry_matches_only_matching_fed_typed_keys(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_registry_entry()])
    loaded = load_canonical_convention_registry(registry_path)

    assert loaded.summary["valid_entry_count"] == 1
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_FED_FOMC,
        event_ticker="KXFED-27APR",
        ticker="KXFED-27APR-T4.25",
        event_slug=None,
        typed_keys=_fed_typed(),
    )
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_FED_FOMC,
        event_ticker="KXFED-27APR",
        ticker="KXFED-27APR-T4.25",
        event_slug=None,
        typed_keys=_fed_typed(source_convention="title_only_guess"),
    ) is None


def test_valid_btc_registry_entry_matches_only_exact_asset_date_source_threshold_operator(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_btc_entry()])
    loaded = load_canonical_convention_registry(registry_path)

    assert loaded.summary["valid_entry_count"] == 1
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTC-26MAY2517",
        ticker="KXBTC-26MAY2517-T86249.99",
        event_slug=None,
        typed_keys=_btc_typed(),
    )
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTC-26MAY2517",
        ticker="KXBTC-26MAY2517-T86250.00",
        event_slug=None,
        typed_keys=_btc_typed(threshold_value=86250.0),
    ) is None


def test_valid_crypto_source_scope_loads_when_source_evidence_is_complete(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_btc_scope_entry()])
    loaded = load_canonical_convention_registry(registry_path)

    assert loaded.summary["valid_entry_count"] == 1
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTCD-26MAY2207",
        ticker="KXBTCD-26MAY2207-T86799.99",
        event_slug=None,
        typed_keys=_btc_typed(
            threshold_value=86799.99,
            measurement_date="May 22, 2026",
            price_source_index="cf benchmarks",
        ),
    )


def test_crypto_scope_can_match_one_exact_event_ticker_only(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [
            _btc_scope_entry(
                applies_to_scope={
                    "venue": "kalshi",
                    "event_ticker_prefix": "KXBTCD",
                    "event_ticker": "KXBTCD-26MAY2217",
                }
            )
        ],
    )
    loaded = load_canonical_convention_registry(registry_path)

    assert loaded.summary["valid_entry_count"] == 1
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTCD-26MAY2217",
        ticker="KXBTCD-26MAY2217-T92499.99",
        event_slug=None,
        typed_keys=_btc_typed(
            threshold_value=92499.99,
            measurement_date="May 22, 2026",
            price_source_index="cf benchmarks",
        ),
    )
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTCD-26MAY2207",
        ticker="KXBTCD-26MAY2207-T86799.99",
        event_slug=None,
        typed_keys=_btc_typed(
            threshold_value=86799.99,
            measurement_date="May 22, 2026",
            price_source_index="cf benchmarks",
        ),
    ) is None


def test_crypto_source_scope_with_todo_url_does_not_match(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [_btc_scope_entry(canonical_source_url="<TODO: confirm with venue rules>", confidence="pending")],
    )
    loaded = load_canonical_convention_registry(registry_path)

    assert loaded.summary["valid_entry_count"] == 0
    assert "invalid_canonical_source_url" in loaded.invalid_entries[0]["blockers"]
    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTCD-26MAY2207",
        ticker="KXBTCD-26MAY2207-T86799.99",
        event_slug=None,
        typed_keys=_btc_typed(
            threshold_value=86799.99,
            measurement_date="May 22, 2026",
            price_source_index="cf benchmarks",
        ),
    ) is None


def test_missing_reviewer_rejects_registry_entry(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_registry_entry(reviewer="")])

    report = build_canonical_convention_registry_audit(
        registry_path=registry_path,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert report["summary"]["valid_entry_count"] == 0
    assert "missing_reviewer" in report["invalid_entries"][0]["blockers"]


def test_broad_scope_rejects_registry_entry(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_registry_entry(applies_to_scope={"venue": "kalshi", "scope": "all"})])

    loaded = load_canonical_convention_registry(registry_path)
    assert loaded.summary["valid_entry_count"] == 0
    assert "overbroad_scope" in loaded.invalid_entries[0]["blockers"]


def test_title_only_evidence_rejects_registry_entry(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [
            _registry_entry(
                evidence_quote_or_excerpt={
                    "kind": "title_only",
                    "text": "Title-only evidence from market question.",
                }
            )
        ],
    )

    loaded = load_canonical_convention_registry(registry_path)
    assert loaded.summary["valid_entry_count"] == 0
    assert "title_only_evidence" in loaded.invalid_entries[0]["blockers"]


def test_graph_or_llm_evidence_rejects_registry_entry(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [
            _registry_entry(
                evidence_quote_or_excerpt={
                    "kind": "llm_hint",
                    "text": "LLM relationship hypothesis says this source is likely.",
                }
            )
        ],
    )

    loaded = load_canonical_convention_registry(registry_path)
    assert loaded.summary["valid_entry_count"] == 0
    assert "graph_or_llm_evidence" in loaded.invalid_entries[0]["blockers"]


def test_planted_hint_evidence_rejects_registry_entry(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(
        registry_path,
        [
            _btc_scope_entry(
                evidence_quote_or_excerpt={
                    "kind": "saved_venue_rules_excerpt",
                    "text": "hint_unreviewed_must_validate_against_venue_rules from a planted source_url_candidate.",
                }
            )
        ],
    )

    loaded = load_canonical_convention_registry(registry_path)
    assert loaded.summary["valid_entry_count"] == 0
    assert "planted_hint_evidence" in loaded.invalid_entries[0]["blockers"]


def test_mismatched_typed_key_does_not_match_registry(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_btc_entry()])
    loaded = load_canonical_convention_registry(registry_path)

    assert match_canonical_registry_entry(
        loaded.valid_entries,
        venue="kalshi",
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        event_ticker="KXBTC-26MAY2517",
        ticker="KXBTC-26MAY2517-T86249.99",
        event_slug=None,
        typed_keys=_btc_typed(price_source_index="coinbase"),
    ) is None


def test_registry_can_upgrade_review_tier_but_not_paper_or_evaluator_by_itself(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_registry_entry()])
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

    report = build_settlement_evidence_burden_report(
        input_dir=tmp_path / "reports",
        registry_path=registry_path,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    row = report["markets"][0]
    assert row["registry_match"]["entry_id"] == "fed-fomc-source-convention"
    assert row["review_readiness_tier"] == TIER_SETTLEMENT_SOURCE_REVIEW_READY
    assert row["quote_freshness_status"]["blocker"] == "missing_quote_captured_at"
    assert row["review_readiness_tier"] != TIER_EXACT_PAYOFF_REVIEW_READY
    assert row["review_readiness_tier"] != TIER_EXECUTION_EVALUATION_READY
    assert row["paper_candidate_emitted"] is False
    assert row["affects_evaluator_gates"] is False


def test_cli_audit_canonical_convention_registry_prints_summary(tmp_path, capsys) -> None:
    registry_path = tmp_path / "registry.json"
    _write_registry(registry_path, [_registry_entry()])

    rc = scan.main(["audit-canonical-convention-registry", "--registry", str(registry_path)])
    stdout = capsys.readouterr().out

    assert rc == 0
    assert "canonical_convention_registry_status=OK" in stdout
    assert "valid=1" in stdout
    assert "invalid=0" in stdout


def test_cli_audit_canonical_convention_registry_writes_json_output(tmp_path, capsys) -> None:
    registry_path = tmp_path / "registry.json"
    json_output = tmp_path / "registry_audit.json"
    _write_registry(registry_path, [_registry_entry()])

    rc = scan.main(
        [
            "audit-canonical-convention-registry",
            "--registry",
            str(registry_path),
            "--json-output",
            str(json_output),
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads(json_output.read_text(encoding="utf-8"))

    assert rc == 0
    assert "canonical_convention_registry_status=OK" in stdout
    assert json_output.exists()
    assert payload["summary"]["valid_entry_count"] == 1
