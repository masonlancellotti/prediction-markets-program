from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.polymarket_clob_taxonomy_refresh import (
    B_EMPTY_BOOK,
    B_MISSING_ASK,
    B_MISSING_ASK_SIZE,
    B_MISSING_BID,
    B_MISSING_BID_SIZE,
    B_MISSING_CLOB_BOOK,
    B_MISSING_TOKEN_ID,
    B_PUBLIC_FETCH_FAILED,
    B_STALE_OR_MISSING_QUOTE,
    PRESERVED_BLOCKERS,
    SHAPE_POINT_IN_TIME,
    refresh_polymarket_clob_for_taxonomy_candidates,
    write_polymarket_clob_taxonomy_refresh_files,
)
from venues.orderbooks import OrderbookClientError


_NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _scout_row(
    *,
    row_id: str,
    market_shape: str = "point_in_time_threshold",
    family: str = "CRYPTO",
    score: float = 50.0,
    token_ids: list[str] | None = None,
    condition_id: str | None = "0xcondabc",
    market_id: str = "1001",
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "market_id": market_id,
        "condition_id": condition_id,
        "event_id": "evt_1",
        "event_slug": "evt-slug",
        "market_slug": "mkt-slug",
        "venue": "polymarket",
        "captured_at": "2026-05-20T00:00:00+00:00",
        "raw_source_file": "fake.json",
        "source_url": "https://polymarket.com/market/mkt-slug",
        "question": f"Will BTC be above $100k on 2026-09-17? ({row_id})",
        "title": None,
        "family": family,
        "raw_taxonomy_shape": "POINT_IN_TIME_THRESHOLD",
        "market_shape": market_shape,
        "typed_keys": {"deadline_or_date": "2026-09-17", "threshold": 100000.0, "comparator": ">"},
        "typed_key_complete": True,
        "settlement_source_present": True,
        "settlement_rules_text_present": True,
        "token_ids": token_ids if token_ids is not None else ["tok_yes", "tok_no"],
        "clob_book_attached": False,
        "clob_book": None,
        "clob_book_fresh": False,
        "blockers": (
            blockers
            if blockers is not None
            else [
                "polymarket_registry_blocks_pair_creation_until_review",
                "missing_clob_book",
                "stale_or_missing_quote",
                "title_only_match_not_equivalence",
            ]
        ),
        "exact_matchability_score": score,
        "allowed_next_action": "WATCH",
        "next_action_text": "Watch.",
        "recommended_pair": "Polymarket_vs_Kalshi_or_CDNA",
        "diagnostic_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_exact_payoff_compatible_with_kalshi": False,
    }


def _scout_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_kind": "polymarket_taxonomy_shape_scout_v1",
        "schema_version": 1,
        "source": "polymarket_taxonomy_shape_scout_v1",
        "generated_at": "2026-05-20T00:00:00+00:00",
        "input_dir": "reports",
        "diagnostic_only": True,
        "summary": {
            "total_rows": len(rows),
            "point_in_time_candidates": sum(
                1 for r in rows if r.get("market_shape") == "point_in_time_threshold"
            ),
            "clob_book_attached": 0,
            "top_blockers": [],
        },
        "rows": rows,
        "warnings": [],
        "safety": {"diagnostic_only": True},
    }


def _write_scout(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "scout.json"
    path.write_text(json.dumps(_scout_payload(rows)), encoding="utf-8")
    return path


def _book(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "bids": [{"price": price, "size": size} for price, size in bids],
        "asks": [{"price": price, "size": size} for price, size in asks],
    }


def test_selects_top_point_in_time_rows_first(tmp_path: Path) -> None:
    rows = [
        _scout_row(row_id="poly_deadline", market_shape="deadline_threshold_touch", score=99.0),
        _scout_row(row_id="poly_macro", market_shape="macro_rate_meeting", score=80.0),
        _scout_row(row_id="poly_pit_low", market_shape="point_in_time_threshold", score=31.0),
        _scout_row(row_id="poly_pit_high", market_shape="point_in_time_threshold", score=70.0),
    ]
    taxonomy_json = _write_scout(tmp_path, rows)
    fetched: list[str] = []

    def fake_fetch(token_id: str) -> dict[str, Any]:
        fetched.append(token_id)
        return _book([(0.45, 1000.0)], [(0.46, 800.0)])

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=10,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=30.0,
        generated_at=_NOW,
        fetch_book=fake_fetch,
    )
    rows_out = bundle["report"]["rows"]
    # Only the two point-in-time rows should be selected; in descending score order.
    assert [r["row_id"] for r in rows_out] == ["poly_pit_high", "poly_pit_low"]
    # Deadline shape never selected (excluded by default).
    excluded = bundle["report"]["excluded_by_reason"]
    assert "deadline_or_range_hit_or_bucket_excluded_by_default" in excluded
    assert excluded["deadline_or_range_hit_or_bucket_excluded_by_default"] == 1


def test_excludes_deadline_and_range_by_default(tmp_path: Path) -> None:
    rows = [
        _scout_row(row_id="poly_deadline", market_shape="deadline_threshold_touch", score=99.0),
        _scout_row(row_id="poly_range_hit", market_shape="range_hit", score=99.0),
        _scout_row(row_id="poly_range_bucket", market_shape="range_bucket", score=99.0),
        _scout_row(row_id="poly_crypto_deadline", market_shape="crypto_deadline_range_hit", score=99.0),
        _scout_row(row_id="poly_all_time_high_by_date", market_shape="all_time_high_by_date", score=99.0),
    ]
    taxonomy_json = _write_scout(tmp_path, rows)

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=10,
        shape_filter="all",
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=lambda token_id: _book([(0.5, 1.0)], [(0.55, 1.0)]),
    )
    # shape_filter='all' but include_deadline_range=False (default) still drops them.
    assert bundle["report"]["rows"] == []
    excluded = bundle["report"]["excluded_by_reason"]
    assert excluded.get("deadline_or_range_hit_or_bucket_excluded_by_default") == 5


def test_include_deadline_range_flag_allows_them(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_deadline", market_shape="deadline_threshold_touch", score=99.0)]
    taxonomy_json = _write_scout(tmp_path, rows)

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=10,
        shape_filter="all",
        min_score=0.0,
        include_deadline_range=True,
        generated_at=_NOW,
        fetch_book=lambda token_id: _book([(0.5, 100.0)], [(0.55, 200.0)]),
    )
    assert len(bundle["report"]["rows"]) == 1


def test_attaches_explicit_bid_ask_size_timestamp_from_fake_book(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_attach", score=60.0, token_ids=["tok_yes", "tok_no"])]
    taxonomy_json = _write_scout(tmp_path, rows)

    def fake_fetch(token_id: str) -> dict[str, Any]:
        if token_id == "tok_yes":
            return _book(
                bids=[(0.40, 750.0), (0.39, 500.0)],
                asks=[(0.42, 600.0), (0.43, 400.0)],
            )
        return _book(bids=[(0.55, 300.0)], asks=[(0.60, 250.0)])

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=5,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=fake_fetch,
    )
    row = bundle["report"]["rows"][0]
    quote = row["attached_quote"]
    assert quote["attached"] is True
    assert quote["token_id"] == "tok_yes"
    assert quote["condition_id"] == "0xcondabc"
    assert quote["bid"] == 0.40
    assert quote["ask"] == 0.42
    assert quote["bid_size"] == 750.0
    assert quote["ask_size"] == 600.0
    assert quote["observed_at"] == _NOW.isoformat()
    assert quote["quote_timestamp"] == _NOW.isoformat()
    assert quote["inferred_from_midpoint_or_complement"] is False
    assert quote["empty_book"] is False
    assert quote["raw_book_file"]
    # Blockers downgraded.
    blockers_after = row["blockers_after"]
    assert B_MISSING_CLOB_BOOK not in blockers_after
    assert B_STALE_OR_MISSING_QUOTE not in blockers_after
    # Preserved blockers remain.
    for preserved in PRESERVED_BLOCKERS:
        assert preserved in blockers_after
    # Two books saved (one per token id).
    assert row["books_requested"] == 2
    assert row["books_saved"] == 2
    # Summary counts.
    s = bundle["report"]["summary"]
    assert s["rows_enriched"] == 1
    assert s["rows_with_bid"] == 1
    assert s["rows_with_ask"] == 1
    assert s["rows_with_bid_ask"] == 1
    assert s["rows_with_bid_ask_size"] == 1
    assert s["rows_with_timestamp"] == 1
    assert s["still_missing_clob"] == 0
    assert s["still_stale_or_missing_quote"] == 0


def test_empty_book_remains_blocked(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_empty", score=60.0)]
    taxonomy_json = _write_scout(tmp_path, rows)

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=5,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=lambda token_id: {"bids": [], "asks": []},
    )
    row = bundle["report"]["rows"][0]
    quote = row["attached_quote"]
    assert quote["attached"] is True
    assert quote["empty_book"] is True
    assert quote["bid"] is None
    assert quote["ask"] is None
    blockers_after = row["blockers_after"]
    assert B_EMPTY_BOOK in blockers_after
    assert B_MISSING_BID in blockers_after
    assert B_MISSING_ASK in blockers_after
    assert B_MISSING_BID_SIZE in blockers_after
    assert B_MISSING_ASK_SIZE in blockers_after
    # An empty book is still 'attached' (we fetched a response), so missing_clob_book
    # is *not* the right blocker — the row is instead blocked by polymarket_clob_empty_book.
    # However stale_or_missing_quote should not be removed because there's no usable quote.
    assert B_MISSING_CLOB_BOOK not in blockers_after  # we got a real response
    for preserved in PRESERVED_BLOCKERS:
        assert preserved in blockers_after
    # Summary counts.
    s = bundle["report"]["summary"]
    assert s["rows_with_bid"] == 0
    assert s["rows_with_ask"] == 0


def test_missing_token_id_remains_blocked(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_no_tok", score=60.0, token_ids=[])]
    taxonomy_json = _write_scout(tmp_path, rows)

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=5,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=lambda token_id: _book([(0.5, 1.0)], [(0.55, 1.0)]),
    )
    row = bundle["report"]["rows"][0]
    assert row["books_requested"] == 0
    assert row["books_saved"] == 0
    assert row["attached_quote"]["attached"] is False
    blockers_after = row["blockers_after"]
    assert B_MISSING_TOKEN_ID in blockers_after
    assert B_MISSING_CLOB_BOOK in blockers_after
    assert B_STALE_OR_MISSING_QUOTE in blockers_after
    for preserved in PRESERVED_BLOCKERS:
        assert preserved in blockers_after


def test_public_fetch_failure_remains_blocked(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_fail", score=60.0, token_ids=["tok_yes"])]
    taxonomy_json = _write_scout(tmp_path, rows)

    def fake_fetch(token_id: str) -> Any:
        raise OrderbookClientError("public CLOB returned HTTP 503")

    bundle = refresh_polymarket_clob_for_taxonomy_candidates(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        max_candidates=5,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=fake_fetch,
    )
    row = bundle["report"]["rows"][0]
    assert row["books_requested"] == 1
    assert row["books_saved"] == 0
    blockers_after = row["blockers_after"]
    assert B_PUBLIC_FETCH_FAILED in blockers_after
    assert B_MISSING_CLOB_BOOK in blockers_after
    assert B_STALE_OR_MISSING_QUOTE in blockers_after
    for preserved in PRESERVED_BLOCKERS:
        assert preserved in blockers_after


def test_no_paper_candidate_emitted_anywhere(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_a", score=60.0), _scout_row(row_id="poly_b", score=55.0)]
    taxonomy_json = _write_scout(tmp_path, rows)

    bundle = write_polymarket_clob_taxonomy_refresh_files(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        json_output=tmp_path / "out_refresh.json",
        enriched_output=tmp_path / "out_enriched.json",
        markdown_output=tmp_path / "out_refresh.md",
        max_candidates=10,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=lambda token_id: _book([(0.5, 100.0)], [(0.55, 100.0)]),
    )
    forbidden = "PAPER" + "_CANDIDATE"
    for path in (
        tmp_path / "out_refresh.json",
        tmp_path / "out_enriched.json",
        tmp_path / "out_refresh.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert forbidden not in text
    report = bundle["report"]
    enriched = bundle["enriched"]
    assert report["summary"]["exact_ready_rows"] == 0
    assert report["summary"]["paper_candidate_rows"] == 0
    for row in report["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["paper_candidate"] is False
        assert row["execution_ready"] is False
        assert row["attached_quote"]["inferred_from_midpoint_or_complement"] is False
    for row in enriched["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["paper_candidate"] is False


def test_no_private_or_auth_strings_in_source_or_outputs(tmp_path: Path) -> None:
    rows = [_scout_row(row_id="poly_x", score=60.0)]
    taxonomy_json = _write_scout(tmp_path, rows)
    write_polymarket_clob_taxonomy_refresh_files(
        taxonomy_json=taxonomy_json,
        output_dir=tmp_path / "out",
        json_output=tmp_path / "out_refresh.json",
        enriched_output=tmp_path / "out_enriched.json",
        markdown_output=tmp_path / "out_refresh.md",
        max_candidates=5,
        shape_filter=SHAPE_POINT_IN_TIME,
        min_score=0.0,
        generated_at=_NOW,
        fetch_book=lambda token_id: _book([(0.5, 1.0)], [(0.55, 1.0)]),
    )
    module_path = Path("relative_value") / "polymarket_clob_taxonomy_refresh.py"
    source_text = module_path.read_text(encoding="utf-8")
    output_text = (
        (tmp_path / "out_refresh.json").read_text(encoding="utf-8")
        + (tmp_path / "out_enriched.json").read_text(encoding="utf-8")
        + (tmp_path / "out_refresh.md").read_text(encoding="utf-8")
    )
    # Strict patterns that would only appear in *real* code touching private endpoints,
    # auth headers, wallet/signing surfaces, or geolocation/Cloudflare-bypass tricks.
    # We do NOT match natural-language descriptions in docstrings.
    forbidden_patterns = (
        '"Authorization"',
        "'Authorization'",
        "Bearer ",
        "X-API-Key",
        "x-api-key",
        "PRIVATE_KEY",
        "private_key=",
        "PRIVATE-KEY",
        "signTypedData",
        "eth_signTypedData",
        "mnemonic",
        "seed_phrase",
        "Cloudflare-Bypass",
        "cloudflare_bypass",
        '"POST"',
        "'POST'",
        "method='POST'",
        'method="POST"',
        "method='DELETE'",
        'method="DELETE"',
        "/auth/api-key",
        "/clob/auth",
        "/order",
        "/orders",
        "/cancel",
        "/cancels",
        "/positions",
        "/balance",
        "/balances",
        "/fills",
        "/trades/me",
    )
    # Real Polymarket public endpoints we DO use must not be on the forbidden list, just to
    # make sure the patterns are tight.
    assert "/book" not in forbidden_patterns
    for forbidden in forbidden_patterns:
        assert forbidden not in source_text, f"forbidden token in module source: {forbidden}"
        assert forbidden not in output_text, f"forbidden token in outputs: {forbidden}"


def test_cli_writes_outputs_with_safe_summary_line(tmp_path: Path, capsys) -> None:
    rows = [
        _scout_row(row_id="poly_cli_a", score=60.0, token_ids=["tok_yes_a", "tok_no_a"]),
        _scout_row(row_id="poly_cli_b", score=55.0, token_ids=["tok_yes_b", "tok_no_b"]),
    ]
    taxonomy_json = _write_scout(tmp_path, rows)

    fetched: list[str] = []

    def fake_fetch(token_id: str) -> dict[str, Any]:
        fetched.append(token_id)
        return _book([(0.40, 100.0)], [(0.42, 100.0)])

    # Inject a fake fetcher by monkey-patching the writer for the CLI test.
    from relative_value import polymarket_clob_taxonomy_refresh as refresh_module

    original = refresh_module.refresh_polymarket_clob_for_taxonomy_candidates

    def wrapped(**kwargs: Any) -> dict[str, Any]:
        kwargs["fetch_book"] = fake_fetch
        return original(**kwargs)

    refresh_module.refresh_polymarket_clob_for_taxonomy_candidates = wrapped  # type: ignore[assignment]
    try:
        result = scan.main(
            [
                "refresh-polymarket-clob-for-taxonomy-candidates",
                "--taxonomy-json",
                str(taxonomy_json),
                "--output-dir",
                str(tmp_path / "snap"),
                "--json-output",
                str(tmp_path / "refresh.json"),
                "--enriched-output",
                str(tmp_path / "enriched.json"),
                "--markdown-output",
                str(tmp_path / "refresh.md"),
                "--max-candidates",
                "10",
                "--shape",
                "point_in_time_threshold",
                "--min-score",
                "0",
            ]
        )
    finally:
        refresh_module.refresh_polymarket_clob_for_taxonomy_candidates = original  # type: ignore[assignment]
    assert result == 0
    stdout = capsys.readouterr().out
    assert "polymarket_clob_taxonomy_refresh=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    payload = json.loads((tmp_path / "refresh.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "polymarket_clob_taxonomy_refresh_v1"
    assert payload["summary"]["candidates_selected"] == 2
    assert payload["summary"]["rows_enriched"] == 2
    enriched = json.loads((tmp_path / "enriched.json").read_text(encoding="utf-8"))
    assert enriched["schema_kind"] == "polymarket_taxonomy_shape_scout_enriched_v1"
    assert enriched["summary"]["paper_candidate_rows"] == 0


def test_ops_status_surfaces_refreshed_clob_coverage(tmp_path: Path) -> None:
    # Synthesize the refresh JSON layout the ops-status reader expects.
    refresh_payload = {
        "schema_kind": "polymarket_clob_taxonomy_refresh_v1",
        "schema_version": 1,
        "source": "polymarket_clob_taxonomy_refresh_v1",
        "generated_at": "2026-05-26T00:00:00+00:00",
        "taxonomy_json": "reports/polymarket_taxonomy_shape_scout.json",
        "snapshot_dir": str(tmp_path / "snap" / "20260526_000000Z"),
        "shape_filter": "point_in_time_threshold",
        "min_score": 30.0,
        "max_candidates": 200,
        "include_deadline_range": False,
        "summary": {
            "candidates_selected": 50,
            "books_requested": 100,
            "books_saved": 92,
            "rows_enriched": 47,
            "rows_with_bid": 47,
            "rows_with_ask": 46,
            "rows_with_bid_ask": 46,
            "rows_with_bid_ask_size": 45,
            "rows_with_timestamp": 47,
            "still_missing_clob": 3,
            "still_stale_or_missing_quote": 3,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "top_remaining_blockers": [
                {"blocker": "polymarket_clob_public_fetch_failed", "count": 3},
                {"blocker": "title_only_match_not_equivalence", "count": 50},
            ],
        },
        "rows": [],
        "excluded_by_reason": {},
        "warnings": [],
        "safety": {"diagnostic_only": True},
    }
    (tmp_path / "polymarket_clob_taxonomy_refresh.json").write_text(
        json.dumps(refresh_payload), encoding="utf-8"
    )
    # Also write a minimal scout file so the ops report does not flag both as missing.
    scout_payload = {
        "schema_kind": "polymarket_taxonomy_shape_scout_v1",
        "schema_version": 1,
        "source": "polymarket_taxonomy_shape_scout_v1",
        "summary": {
            "total_rows": 0,
            "clob_book_attached": 0,
            "point_in_time_candidates": 0,
            "deadline_or_range_hit_blocked": 0,
            "typed_key_complete": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "shape_counts": {},
            "top_blockers": [],
        },
        "rows": [],
        "safety": {},
        "input_dir": "reports",
        "generated_at": "2026-05-26T00:00:00+00:00",
    }
    (tmp_path / "polymarket_taxonomy_shape_scout.json").write_text(
        json.dumps(scout_payload), encoding="utf-8"
    )

    from relative_value.relative_value_ops_status import (
        build_relative_value_ops_status_report,
        render_relative_value_ops_status_markdown,
    )

    report = build_relative_value_ops_status_report(
        input_dir=tmp_path, generated_at=datetime(2026, 5, 26, tzinfo=timezone.utc)
    )
    block = report["summary"]["polymarket_clob_taxonomy_refresh"]
    assert block["present"] is True
    assert block["candidates_selected"] == 50
    assert block["rows_enriched"] == 47
    assert block["rows_with_bid_ask"] == 46
    assert block["still_missing_clob"] == 3
    assert block["exact_ready_rows"] == 0
    assert block["paper_candidate_rows"] == 0
    md = render_relative_value_ops_status_markdown(report)
    assert "polymarket_clob_taxonomy_refresh" in md
    assert "rows_enriched: `47`" in md
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in md
