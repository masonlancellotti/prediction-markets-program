"""Saved-file-only manual-evidence requirements catalogue + playbook generator.

Emits a curated, vertical-by-platform catalogue of every manual evidence item
Mason has to collect to move Crypto / Economics / Sports rows from missing-
evidence into source-review or manual-review. Each item carries:

- evidence_id (stable kebab-case identifier)
- vertical (crypto / economics / sports / cross_cutting)
- platform + platform_status (active / queued / reference / official_source)
- market_family + specific market hint
- required_field + missing-or-uncertain value description
- why_it_matters + the exact blocker the captured value would clear
- exact_gate_affected (none / family_typed / source_review / exact_review /
  paper_review) plus boolean ``can_ever_affect_exact_review`` and
  ``can_ever_affect_paper_review`` — both can only be ``true`` *after* the
  current evaluator/exact gates already pass; manual evidence on its own
  never moves rows past source-review.
- collection_method (public_page / logged_in_ui / api_or_saved_file /
  official_rulebook_pdf / order_preview_no_submission / official_source_truth_feed)
- exact_manual_steps (a small, literal numbered list)
- screenshot_or_text_to_capture
- source_url_hint or report_path_hint
- validation_command (an existing safe repo command name, or ``none``)
- repeat_cadence
- priority (P0 / P1 / P2 / P3 / P4)
- status (missing / partially_captured / captured_unreviewed /
  validated_diagnostic / reviewed_usable / blocked)
- fake_edge_risk (one-sentence summary of how this evidence could be
  *misused* to create a false-positive arb)

Hard safety constraints respected by this module:
- Saved files only. No live API calls.
- Diagnostic only. Every item carries ``can_create_candidate_pair=false`` and
  ``can_create_paper_candidate=false``.
- Queued IBKR / SX Bet / ProphetX items always carry
  ``platform_status=queued`` and ``status != reviewed_usable``; the catalogue
  itself never marks them as active.
- Reference-only (Odds API, official truth feeds) items always carry
  ``can_ever_affect_exact_review=false``; they can colour fair-value but
  never sit on either side of an exact pairing.
- No PAPER_CANDIDATE forced or implied.
- No evaluator / exact gates lowered, cleared, or shorted.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SCHEMA_KIND = "manual_evidence_requirements_v1"
REPORT_SOURCE = "manual_evidence_requirements_v1"

# Saved-report inputs the builder consults to set ``current_value_if_known``
# and ``status`` on each catalogue item. Missing files are tolerated; the item
# falls back to its default status (typically ``missing`` or
# ``partially_captured``).
_INPUT_FILES = {
    "kalshi_crypto_typed_key_audit": "kalshi_crypto_typed_key_audit.json",
    "polymarket_taxonomy_shape_scout": "polymarket_taxonomy_shape_scout.json",
    "polymarket_taxonomy_shape_scout_enriched": "polymarket_taxonomy_shape_scout_enriched.json",
    "polymarket_point_in_time_typed_key_audit": "polymarket_point_in_time_typed_key_audit.json",
    "crypto_payoff_calendar_audit": "crypto_payoff_calendar_audit.json",
    "cdna_crypto_basis_risk_scout": "cdna_crypto_basis_risk_scout.json",
    "crypto_com_predict_cdna_research_snapshot": "crypto_com_predict_cdna_research_snapshot.json",
    "family_graduation_fed": "family_graduation_fed.json",
    "default_sports_sweep_summary": "default_sports_sweep_summary.json",
    "ibkr_forecastex_quote_diagnostics": "ibkr_forecastex_quote_diagnostics.json",
    "the_odds_api_fv_residuals": "the_odds_api_fv_residuals.json",
    "core_trio_peer_coverage_audit": "core_trio_peer_coverage_audit.json",
    "relative_value_ops_status": "relative_value_ops_status.json",
}


_VALID_VERTICALS = frozenset({"crypto", "economics", "sports", "cross_cutting"})
_VALID_PLATFORMS = frozenset(
    {
        "kalshi",
        "polymarket",
        "cdna",
        "ibkr_forecastex",
        "sx_bet",
        "prophetx",
        "the_odds_api",
        "official_source",
        "cross_venue",
    }
)
_VALID_PLATFORM_STATUSES = frozenset({"active", "queued", "reference", "official_source"})
_VALID_PRIORITIES = ("P0", "P1", "P2", "P3", "P4")
_VALID_STATUSES = frozenset(
    {
        "missing",
        "partially_captured",
        "captured_unreviewed",
        "validated_diagnostic",
        "reviewed_usable",
        "blocked",
    }
)
_VALID_REPEAT_CADENCES = frozenset(
    {
        "one_time_per_family",
        "per_market",
        "per_contract_month",
        "per_event",
        "per_rules_version",
        "per_fee_schedule_change",
        "per_trading_session",
    }
)
_VALID_COLLECTION_METHODS = frozenset(
    {
        "public_page",
        "logged_in_ui",
        "api_or_saved_file",
        "official_rulebook_pdf",
        "order_preview_no_submission",
        "official_source_truth_feed",
    }
)
_VALID_GATES = frozenset(
    {
        "none",
        "family_typed",
        "source_review",
        "exact_review",
        "paper_review",
    }
)


# ---------------------------------------------------------------------------
# Curated catalogue
# ---------------------------------------------------------------------------


# Helpers for compact item construction.
def _item(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "specific_market": None,
        "ticker_url_or_report_row": None,
        "current_value_if_known": None,
        "exact_gate_affected": "none",
        "can_ever_affect_exact_review": False,
        "can_ever_affect_paper_review": False,
        "validation_command": "none",
        "fake_edge_risk": "Manual evidence on its own never clears gates; if mis-recorded the row would still be blocked by upstream typed-key / source / quote-freshness gates before any paper review.",
    }
    defaults.update(kwargs)
    return defaults


def _crypto_items() -> list[dict[str, Any]]:
    return [
        _item(
            evidence_id="cry-kal-hourly-rules-text",
            vertical="crypto",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_btc_eth_hourly_point_in_time",
            specific_market="any KXBTC-* / KXETH-* hourly ticker",
            ticker_url_or_report_row="reports/kalshi_crypto_typed_key_audit.json",
            required_field="settlement_rules_text + observation_time + timezone",
            missing_or_uncertain_value="captured but not human-reviewed for comparator strictness ('above' vs 'at or above')",
            why_it_matters="hourly point-in-time vs deadline-touch must be distinguishable from rules text alone; mis-reading comparator strictness flips a near-the-money pair",
            blocker_it_clears="comparator_missing OR kalshi_crypto_missing_comparator (clarifies strict vs inclusive)",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open the saved Kalshi rules_text_preview field on a representative KXBTC / KXETH hourly row.",
                "Confirm the exact phrasing 'is above X at H PM/AM EDT on DATE'.",
                "Record whether 'above' means strict (>) or whether the rules ever say 'at or above' (>=).",
                "Record the observation 60-second averaging window for CF Benchmarks BRTI/ERTI.",
            ],
            screenshot_or_text_to_capture="screenshot of the Kalshi rules page + paste of the rules text into a manual memo",
            source_url_hint="https://kalshi.com/markets/<event_ticker>",
            validation_command="kalshi-crypto-typed-key-audit",
            repeat_cadence="per_rules_version",
            priority="P1",
            status="captured_unreviewed",
        ),
        _item(
            evidence_id="cry-kal-5pm-daily-rules",
            vertical="crypto",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_btc_daily_5pm_close",
            specific_market="KXBTC-*-17 (the 17 = 5pm EDT close)",
            ticker_url_or_report_row="reports/crypto_payoff_calendar_audit.json -> shape=daily_5pm_price_threshold",
            required_field="confirmation that 21:00 UTC == 5 PM EDT and that the index averaging window is the 60s before 5pm",
            missing_or_uncertain_value="rules text references '5 PM EDT' explicitly but the source URL for CF Benchmarks BRTI methodology is not saved",
            why_it_matters="cross-venue compatibility against any Polymarket 5pm market requires confirming both venues use the same daily close index and observation window",
            blocker_it_clears="settlement_source_missing AND/OR settlement_source_mismatch (versus any 5pm Polymarket peer)",
            exact_gate_affected="source_review",
            collection_method="official_rulebook_pdf",
            exact_manual_steps=[
                "Open the CF Benchmarks BRTI methodology page; save the PDF.",
                "Note the 60-second averaging window definition and price-source exchanges.",
                "Save the PDF locally outside the repo (do NOT add credentials/files into the repo).",
                "Record the methodology version date in the manual memo.",
            ],
            screenshot_or_text_to_capture="CF Benchmarks BRTI methodology cover page + window-definition section",
            source_url_hint="https://www.cfbenchmarks.com/data/indices/BRTI",
            validation_command="crypto-payoff-calendar-audit",
            repeat_cadence="per_rules_version",
            priority="P1",
            status="missing",
        ),
        _item(
            evidence_id="cry-kal-weekly-friday-rules",
            vertical="crypto",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_btc_weekly_friday_close",
            specific_market="KXBTC-* with target_date on a Friday",
            ticker_url_or_report_row="reports/crypto_payoff_calendar_audit.json -> shape=weekly_friday_close_threshold",
            required_field="explicit Friday close at 21:00 UTC; rules text identical to the daily 5pm close",
            missing_or_uncertain_value="whether weekly Friday markets share the same KXBTC/KXBTCD ticker family and same BRTI methodology",
            why_it_matters="if same family + same source, weekly-Friday rows pair exact-shape with daily-5pm-on-Friday rows; if different family they don't",
            blocker_it_clears="payoff_shape_mismatch (when comparing same-Friday daily 5pm against weekly Friday close)",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Filter the saved Kalshi crypto audit rows to ones with target_date weekday=Friday.",
                "Compare rules text of a weekly Friday ticker against the daily 5pm ticker on the same date.",
                "Confirm both use 'CF Benchmarks BRTI before 5 PM EDT at 5 PM EDT on <Friday>'.",
            ],
            screenshot_or_text_to_capture="text diff of rules text between the two ticker families on the same Friday",
            source_url_hint="https://kalshi.com/markets/KXBTC",
            validation_command="crypto-payoff-calendar-audit",
            repeat_cadence="one_time_per_family",
            priority="P2",
            status="partially_captured",
        ),
        _item(
            evidence_id="cry-kal-range-bucket-inclusive",
            vertical="crypto",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_btc_eth_range_bucket",
            specific_market="KXBTC-*-B<value> tickers (-B = below-bound)",
            ticker_url_or_report_row="reports/crypto_payoff_calendar_audit.json -> shape=range_bucket_at_time",
            required_field="confirm whether 'between L-U' bucket endpoints are inclusive on both sides or strict on one",
            missing_or_uncertain_value="bucket inclusivity (left-closed vs right-open vs both-inclusive)",
            why_it_matters="if mis-read, summed Yes prices across adjacent buckets cross 100% which would falsely imply arbitrage",
            blocker_it_clears="comparator_missing (range-bucket inclusivity is implicitly a comparator)",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open a Kalshi crypto range-bucket market page.",
                "Save the 'How will this be resolved?' rules text.",
                "Read the bucket inclusivity statement explicitly; if absent, mark as blocked.",
            ],
            screenshot_or_text_to_capture="full bucket inclusivity sentence; if not present, screenshot of full rules page",
            source_url_hint="https://kalshi.com/markets/<event_ticker>",
            validation_command="crypto-payoff-calendar-audit",
            repeat_cadence="one_time_per_family",
            priority="P2",
            status="captured_unreviewed",
            fake_edge_risk="Mis-recording inclusivity on adjacent range buckets can make Yes prices appear to sum >100% — false-positive arbitrage that the evaluator would still reject for missing exact-payoff equivalence, but it would consume manual review time.",
        ),
        _item(
            evidence_id="cry-kal-fresh-orderbook",
            vertical="crypto",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_btc_eth_orderbook_depth",
            specific_market="every crypto market in scope for review",
            ticker_url_or_report_row="reports/kalshi_orderbook_enriched_snapshot.json",
            required_field="best_bid / best_ask / size + capture timestamp from a fresh snapshot",
            missing_or_uncertain_value="rows_with_quote = 0 across most crypto rows in current audit",
            why_it_matters="without explicit top-of-book size, no exact-review pair can ever cross the depth gate",
            blocker_it_clears="quote_missing AND stale_or_missing_quote",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Run enrich-kalshi-orderbooks with --snapshot reports/kalshi_markets_snapshot.json --output reports/kalshi_orderbook_enriched_snapshot.json --timeout-seconds 10.",
                "Inspect orderbook_enrichment block for fetch_failed_by_reason; expect closed_or_settled_empty_book for settled markets.",
                "Re-capture the underlying kalshi_markets_snapshot.json daily so settled rows are dropped.",
            ],
            screenshot_or_text_to_capture="orderbook_enrichment.fetch_failed_by_reason counter",
            source_url_hint="reports/kalshi_orderbook_enriched_snapshot.json",
            validation_command="enrich-kalshi-orderbooks",
            repeat_cadence="per_trading_session",
            priority="P0",
            status="partially_captured",
        ),
        _item(
            evidence_id="cry-kal-fee-schedule",
            vertical="crypto",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_btc_eth_fee_schedule",
            specific_market="all crypto markets",
            ticker_url_or_report_row="docs/CODEX_HANDOFF.md (fee evidence section)",
            required_field="effective fee per side (maker + taker + settlement) for crypto category",
            missing_or_uncertain_value="documented fee evidence per market category is referenced in docs but not stored as a per-market JSON",
            why_it_matters="paper-review net-gap threshold subtracts maker+taker+settlement on both sides; mis-recording fees blows up the paper edge",
            blocker_it_clears="missing_fee_metadata (fee evaluator gate)",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="logged_in_ui",
            exact_manual_steps=[
                "Open a Kalshi crypto market in a logged-in browser session.",
                "Hover the order entry preview (no submission) to read maker + taker fee strings.",
                "Record settlement fee from Kalshi's fee disclosure page.",
                "Save a JSON memo with all three values + capture date.",
            ],
            screenshot_or_text_to_capture="order-entry fee preview screenshot (DO NOT submit the order)",
            source_url_hint="https://kalshi.com/about/fees",
            validation_command="none",
            repeat_cadence="per_fee_schedule_change",
            priority="P2",
            status="missing",
        ),
        _item(
            evidence_id="cry-poly-hit-touch-rules-text",
            vertical="crypto",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_btc_hit_or_touch_by_date",
            specific_market="any 'Will Bitcoin hit $X by DATE?' market",
            ticker_url_or_report_row="reports/crypto_payoff_calendar_audit.json -> shape=deadline_touch_threshold",
            required_field="full rules: tick vs candle close, source exchange, observation window endpoints",
            missing_or_uncertain_value="whether 'hit' means a 1-second tick at any time before the deadline or a 1-minute candle close at or above X",
            why_it_matters="the difference between 'tick touch' and 'candle close above' changes both fair value and any peer compatibility class",
            blocker_it_clears="polymarket_rules_missing AND deadline_touch_not_close_price (downgrades to manual-rules-needed only)",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open the Polymarket public event URL for the row.",
                "Copy the entire 'How will this resolve?' rules text into a manual memo.",
                "Tag whether the resolution uses Binance, Coinbase, or Chainlink, and whether it is tick-based or candle-based.",
                "Record clobTokenIds for both YES and NO outcomes if shown.",
            ],
            screenshot_or_text_to_capture="full rules text + clobTokenIds + Polymarket event URL",
            source_url_hint="https://polymarket.com/event/<slug>",
            validation_command="crypto-payoff-calendar-audit",
            repeat_cadence="per_market",
            priority="P1",
            status="missing",
            fake_edge_risk="If 'tick touch' rules are mis-recorded as 'candle close', a Polymarket NO above 50¢ versus a Kalshi 5pm-close YES looks like easy arbitrage even though it is basis-risk only.",
        ),
        _item(
            evidence_id="cry-poly-up-down-rules",
            vertical="crypto",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_btc_up_or_down_today",
            specific_market="'Bitcoin Up or Down - <Date> <window>' markets",
            ticker_url_or_report_row="reports/crypto_payoff_calendar_audit.json -> shape=daily_direction_up_down",
            required_field="open-vs-close definition: open-to-close, prev-close-to-close, or last-trade-to-trade",
            missing_or_uncertain_value="open-price reference and source index (Chainlink vs Binance) per market",
            why_it_matters="up/down direction is a strictly different observable from a threshold close; without the open/close definition the row cannot even be compared to another up/down market",
            blocker_it_clears="open_close_reference_missing AND daily_direction_rules_missing",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open the Polymarket Up or Down event page.",
                "Copy the entire rules section verbatim.",
                "Specifically tag the open-price source + timestamp and the close-price source + timestamp.",
            ],
            screenshot_or_text_to_capture="rules text + the two timestamps (open + close) and the index name",
            source_url_hint="https://polymarket.com/event/<up-or-down-slug>",
            validation_command="crypto-payoff-calendar-audit",
            repeat_cadence="per_market",
            priority="P2",
            status="missing",
            fake_edge_risk="Pairing an up/down market against a Kalshi 5pm threshold would be illegal as exact equivalence; without the rules text the planner cannot even prove it is basis-risk-only.",
        ),
        _item(
            evidence_id="cry-poly-point-in-time-existence",
            vertical="crypto",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_btc_eth_point_in_time_close",
            specific_market="hypothetical 'BTC above $X at 5pm ET on <weekday>' Polymarket market",
            ticker_url_or_report_row="(none — discovery target)",
            required_field="existence-or-absence of any Polymarket close-price market aligned with Kalshi's daily-5pm or weekly-Friday grid",
            missing_or_uncertain_value="no such row exists in saved data; targeted discovery has not found one either",
            why_it_matters="this is the kill-or-confirm investigation for the Kalshi-vs-Polymarket crypto lane — if zero such markets exist on Polymarket, the lane is closed",
            blocker_it_clears="no_current_peer (closes the inquiry) OR creates a new exact_shape_possible row (opens the lane)",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open polymarket.com and search for 'bitcoin May 29' (or any Friday Kalshi sells).",
                "Filter to active crypto markets.",
                "Record yes/no whether any market with explicit '5pm ET' or '4pm ET' close exists.",
                "If yes, capture the slug + rules text + clobTokenIds and run discover-polymarket-crypto-markets --query.",
            ],
            screenshot_or_text_to_capture="screenshot of Polymarket search results + (if found) full rules text",
            source_url_hint="https://polymarket.com/markets/crypto",
            validation_command="discover-polymarket-crypto-markets",
            repeat_cadence="one_time_per_family",
            priority="P0",
            status="missing",
        ),
        _item(
            evidence_id="cry-poly-clob-token-ids",
            vertical="crypto",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_clob_token_ids_for_crypto",
            specific_market="every Polymarket crypto market kept for review",
            ticker_url_or_report_row="reports/polymarket_taxonomy_shape_scout_enriched.json -> token_ids",
            required_field="clobTokenIds for YES and NO outcomes",
            missing_or_uncertain_value="many crypto rows have token_ids=[]; refresh-polymarket-clob-for-taxonomy-candidates cannot attach quotes without token IDs",
            why_it_matters="public no-auth Polymarket CLOB book lookups require token_id; without it the row has no executable depth diagnostic at all",
            blocker_it_clears="polymarket_missing_token_id AND polymarket_missing_clob_quote",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Run refresh-polymarket-clob-for-taxonomy-candidates with the saved taxonomy JSON.",
                "For rows where token_ids are still empty, open the Polymarket event UI and copy clobTokenIds from the network panel (no auth).",
                "Patch token_ids into a manual fixture file under reports/manual_snapshots/polymarket_crypto/.",
            ],
            screenshot_or_text_to_capture="clobTokenIds array (string of two hex/decimal IDs)",
            source_url_hint="https://polymarket.com/event/<slug>",
            validation_command="refresh-polymarket-clob-for-taxonomy-candidates",
            repeat_cadence="per_market",
            priority="P1",
            status="partially_captured",
        ),
        _item(
            evidence_id="cry-cdna-eth-pit-fixture",
            vertical="crypto",
            platform="cdna",
            platform_status="active",
            market_family="cdna_eth_point_in_time_threshold",
            specific_market="ETH 9:00 AM ET 23 May 2026 strike ladder",
            ticker_url_or_report_row="reports/crypto_com_predict_cdna_research_snapshot.json",
            required_field="full Crypto.com Predict event page snapshot + Nadex rule number + ETH index source URL",
            missing_or_uncertain_value="6 CDNA ETH PIT rows exist but settlement_source_url is null and rule version not captured",
            why_it_matters="Nadex ETH Index is a different index than Kalshi's CF Benchmarks ERTI; without the rule version the lane cannot even be tagged basis-risk-only with confidence",
            blocker_it_clears="cdna_rules_missing AND settlement_source_unverified",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open the Crypto.com Predict event page for an ETH point-in-time market.",
                "Save the entire HTML page locally under reports/manual_snapshots/cdna/.",
                "Record the Nadex rule number cited in the resolution criteria (e.g. 'CDNA Rule 14.72 / Nadex ETH Index').",
                "Run parse-crypto-com-predict-cdna-fixtures to regenerate the research snapshot.",
            ],
            screenshot_or_text_to_capture="full event page HTML + the rule-number citation",
            source_url_hint="https://web.crypto.com/explore/predict/events/details/<slug>",
            validation_command="parse-crypto-com-predict-cdna-fixtures",
            repeat_cadence="per_event",
            priority="P1",
            status="captured_unreviewed",
        ),
        _item(
            evidence_id="cry-cdna-btc-pit-fixture",
            vertical="crypto",
            platform="cdna",
            platform_status="active",
            market_family="cdna_btc_point_in_time_threshold",
            specific_market="any CDNA BTC point-in-time event on a Kalshi-matching date",
            ticker_url_or_report_row="(none — discovery target on a Kalshi-aligned date)",
            required_field="rules text + Nadex BTC Index source",
            missing_or_uncertain_value="no CDNA BTC PIT row currently saved on a date Kalshi also lists",
            why_it_matters="the only honest CDNA+Kalshi BTC pairing is a same-asset/date/threshold point-in-time; absent that the lane is purely diagnostic",
            blocker_it_clears="no_current_peer (cdna<->kalshi BTC PIT) OR cdna_rules_missing",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Browse Crypto.com Predict for upcoming BTC point-in-time events on Kalshi-aligned dates (next 2 weeks).",
                "Save the public event page snapshot for any candidate match.",
                "Record the Nadex rule reference.",
                "Re-run parse-crypto-com-predict-cdna-fixtures.",
            ],
            screenshot_or_text_to_capture="event page HTML + Nadex rule reference",
            source_url_hint="https://web.crypto.com/explore/predict/events",
            validation_command="parse-crypto-com-predict-cdna-fixtures",
            repeat_cadence="per_event",
            priority="P2",
            status="missing",
        ),
        _item(
            evidence_id="cry-cdna-ath-rules",
            vertical="crypto",
            platform="cdna",
            platform_status="active",
            market_family="cdna_all_time_high_by_date",
            specific_market="any CDNA all-time-high-by-date event",
            ticker_url_or_report_row="reports/crypto_com_predict_cdna_research_snapshot.json (market_type=all_time_high_by_date)",
            required_field="whether ATH means rolling all-time vs ATH since a specific anchor date",
            missing_or_uncertain_value="rule version + anchor date not saved",
            why_it_matters="ATH-by-date is always basis-risk-only versus a threshold close, but the rule version still matters for any same-shape ATH vs ATH comparison",
            blocker_it_clears="cdna_rules_missing for ATH family",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open a CDNA ATH event page.",
                "Save the rules text verbatim.",
                "Note whether the rule references an absolute anchor date or rolling history.",
            ],
            screenshot_or_text_to_capture="ATH resolution-criteria paragraph",
            source_url_hint="https://web.crypto.com/explore/predict/events",
            validation_command="parse-crypto-com-predict-cdna-fixtures",
            repeat_cadence="per_rules_version",
            priority="P3",
            status="missing",
        ),
        _item(
            evidence_id="cry-cdna-year-end-range-bucket",
            vertical="crypto",
            platform="cdna",
            platform_status="active",
            market_family="cdna_year_end_range_bucket",
            specific_market="'Bitcoin Price at the End of 2026' / similar",
            ticker_url_or_report_row="reports/crypto_com_predict_cdna_research_snapshot.json (market_type=year_end_range_bucket)",
            required_field="exact 11:59 PM ET observation procedure + bucket inclusivity",
            missing_or_uncertain_value="end-of-year observation method (last trade vs index close) not saved",
            why_it_matters="year-end range buckets are the largest CDNA family but cannot be paired against anything until inclusivity + observation method are captured",
            blocker_it_clears="cdna_rules_missing for year-end range",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open a year-end range-bucket event page.",
                "Save rules text verbatim, especially the 11:59 PM ET observation phrasing.",
                "Note the bucket inclusivity rule and the named price index.",
            ],
            screenshot_or_text_to_capture="full rules paragraph + price-index name",
            source_url_hint="https://web.crypto.com/explore/predict/events",
            validation_command="parse-crypto-com-predict-cdna-fixtures",
            repeat_cadence="per_rules_version",
            priority="P3",
            status="missing",
        ),
        _item(
            evidence_id="cry-cdna-earliest-touch-rules",
            vertical="crypto",
            platform="cdna",
            platform_status="active",
            market_family="cdna_earliest_timeframe_threshold_touch",
            specific_market="'When will Bitcoin reach $X first?' style CDNA markets",
            ticker_url_or_report_row="reports/crypto_com_predict_cdna_research_snapshot.json (market_type=earliest_timeframe_threshold_touch)",
            required_field="rule definition for 'first to touch' window endpoints + observation method",
            missing_or_uncertain_value="touch granularity (tick vs minute candle) not captured",
            why_it_matters="touch family is basis-risk-only against close-price markets; rule capture only matters for CDNA-vs-CDNA touch comparisons",
            blocker_it_clears="cdna_rules_missing for earliest-touch family",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open an earliest-timeframe touch event page.",
                "Save full rules.",
                "Note tick vs minute-candle resolution + source index.",
            ],
            screenshot_or_text_to_capture="full rules paragraph",
            source_url_hint="https://web.crypto.com/explore/predict/events",
            validation_command="parse-crypto-com-predict-cdna-fixtures",
            repeat_cadence="per_rules_version",
            priority="P4",
            status="missing",
        ),
    ]


def _economics_items() -> list[dict[str, Any]]:
    return [
        _item(
            evidence_id="eco-kal-fomc-meeting-date",
            vertical="economics",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_fomc_rate_decision",
            specific_market="every KXFED meeting market",
            ticker_url_or_report_row="reports/family_graduation_fed.json (228 candidate rows)",
            required_field="FOMC meeting date + statement release time + revision rules",
            missing_or_uncertain_value="meeting_date already typed for 218/228 rows; release_time + revision rules not stored",
            why_it_matters="cross-venue Fed comparisons need a single authoritative meeting date + statement release moment to align observation windows",
            blocker_it_clears="missing_typed_key:rate_bound for the 10 rows that still lack it",
            exact_gate_affected="family_typed",
            collection_method="official_source_truth_feed",
            exact_manual_steps=[
                "Open the Federal Reserve FOMC calendar page.",
                "Record each upcoming meeting date and 2:00 PM ET statement release time.",
                "Save to a manual JSON fixture under reports/manual_snapshots/fomc/.",
            ],
            screenshot_or_text_to_capture="FOMC meeting calendar table + statement release-time policy",
            source_url_hint="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            validation_command="none",
            repeat_cadence="per_event",
            priority="P1",
            status="captured_unreviewed",
        ),
        _item(
            evidence_id="eco-kal-fomc-rate-bound-definition",
            vertical="economics",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_fomc_rate_decision",
            specific_market="each KXFED ticker family",
            ticker_url_or_report_row="reports/family_graduation_fed.json -> missing_typed_key:rate_bound (10 rows)",
            required_field="rate_bound (upper / lower / midpoint / effective_federal_funds_rate)",
            missing_or_uncertain_value="rate_bound field not captured for 10 rows; without it the family-typed gate stays blocked",
            why_it_matters="Kalshi vs Polymarket vs IBKR FOMC markets often differ on bound; an upper-bound Kalshi market cannot be paired with a midpoint Polymarket market",
            blocker_it_clears="missing_typed_key:rate_bound",
            exact_gate_affected="family_typed",
            collection_method="public_page",
            exact_manual_steps=[
                "Open each KXFED rules page.",
                "Find the explicit wording 'upper bound', 'lower bound', 'midpoint', or 'effective federal funds rate'.",
                "Record to a manual fixture; never default to 'upper bound'.",
            ],
            screenshot_or_text_to_capture="rules paragraph with the bound-keyword",
            source_url_hint="https://kalshi.com/markets/KXFED",
            validation_command="family-graduation",
            repeat_cadence="per_market",
            priority="P0",
            status="missing",
            fake_edge_risk="Defaulting an unspecified rate_bound to 'upper' makes a Polymarket midpoint contract look identically-payoff to a Kalshi upper-bound contract — a structural false-positive arb that the evaluator currently still rejects, but only at the exact-payoff layer.",
        ),
        _item(
            evidence_id="eco-kal-fomc-comparator-strictness",
            vertical="economics",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_fomc_rate_decision",
            specific_market="each KXFED threshold ticker",
            ticker_url_or_report_row="reports/family_graduation_fed.json -> missing_typed_key:threshold_percent",
            required_field="comparator strictness (> vs >=) for the threshold_percent",
            missing_or_uncertain_value="comparator + threshold_percent not captured for 10 rows",
            why_it_matters="strict vs inclusive comparison at the bound flips the resolution when the Fed lands exactly on the threshold",
            blocker_it_clears="missing_typed_key:threshold_percent (and comparator)",
            exact_gate_affected="family_typed",
            collection_method="public_page",
            exact_manual_steps=[
                "Read each Kalshi FOMC rules page.",
                "Record threshold_percent and exact comparator wording (above / at-or-above / below / at-or-below).",
            ],
            screenshot_or_text_to_capture="rules text excerpt with the comparator phrasing",
            source_url_hint="https://kalshi.com/markets/KXFED",
            validation_command="family-graduation",
            repeat_cadence="per_market",
            priority="P0",
            status="missing",
        ),
        _item(
            evidence_id="eco-kal-fomc-fresh-quote",
            vertical="economics",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_fomc_orderbook_depth",
            specific_market="each KXFED market in scope",
            ticker_url_or_report_row="reports/family_graduation_fed.json (missing_quote_captured_at = 456)",
            required_field="best_bid / best_ask / size + capture timestamp",
            missing_or_uncertain_value="quote freshness/depth missing for every FOMC row (228 rows blocked)",
            why_it_matters="paper-review gate requires fresh depth; without it no Fed pair can pass the quote-freshness threshold",
            blocker_it_clears="missing_quote_captured_at AND missing_quote_depth_or_freshness",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Refresh reports/kalshi_markets_snapshot.json (or a targeted Fed snapshot) so the rows have current end_dates.",
                "Run enrich-kalshi-orderbooks with the new snapshot + a 1-hour max-snapshot-age.",
                "Inspect orderbook_enrichment.fetch_failed_by_reason for closed_or_settled / empty rows; drop those before review.",
            ],
            screenshot_or_text_to_capture="orderbook_enrichment.fetch_failed_by_reason counter + the new captured_at",
            source_url_hint="reports/kalshi_orderbook_enriched_snapshot.json",
            validation_command="enrich-kalshi-orderbooks",
            repeat_cadence="per_trading_session",
            priority="P0",
            status="missing",
        ),
        _item(
            evidence_id="eco-kal-fomc-fee-schedule",
            vertical="economics",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_fomc_fee_schedule",
            specific_market="every KXFED market",
            ticker_url_or_report_row="docs/CODEX_HANDOFF.md (fee evidence section)",
            required_field="effective per-side fee for KXFED category",
            missing_or_uncertain_value="fee value not stored per-market",
            why_it_matters="net-gap evaluator subtracts maker+taker+settlement on both sides",
            blocker_it_clears="missing_fee_metadata for KXFED rows",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="logged_in_ui",
            exact_manual_steps=[
                "Open a KXFED market with order entry (no submission).",
                "Read maker + taker fee preview values.",
                "Save them to a manual fee fixture.",
            ],
            screenshot_or_text_to_capture="fee preview screenshot from a logged-in order panel",
            source_url_hint="https://kalshi.com/about/fees",
            validation_command="none",
            repeat_cadence="per_fee_schedule_change",
            priority="P2",
            status="missing",
        ),
        _item(
            evidence_id="eco-poly-fomc-existence",
            vertical="economics",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_fomc_rate_decision",
            specific_market="any Polymarket Fed/FOMC active market",
            ticker_url_or_report_row="(none — Polymarket Fed coverage not in saved taxonomy as 'CRYPTO' family; needs targeted PM search)",
            required_field="existence-or-absence of a Polymarket FOMC market matching a Kalshi meeting + bound + threshold",
            missing_or_uncertain_value="no Polymarket FOMC market currently mapped to a Kalshi peer",
            why_it_matters="this is the kill-or-confirm for the Kalshi-vs-Polymarket Fed lane",
            blocker_it_clears="no_current_peer for FOMC family",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Run discover-polymarket-crypto-markets --query 'fed rate decision' (the query path also works for non-crypto categories).",
                "Browse polymarket.com/markets/politics and search 'fed' for any current meeting markets.",
                "Record slug + rules text + clobTokenIds.",
                "Compare bound + threshold to a Kalshi peer on the same meeting date.",
            ],
            screenshot_or_text_to_capture="Polymarket Fed event slug + rules text + token IDs",
            source_url_hint="https://polymarket.com/markets/politics",
            validation_command="discover-polymarket-crypto-markets",
            repeat_cadence="per_event",
            priority="P1",
            status="missing",
        ),
        _item(
            evidence_id="eco-poly-fomc-bound-alignment",
            vertical="economics",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_fomc_rate_decision",
            specific_market="(any Polymarket Fed market discovered above)",
            ticker_url_or_report_row="(none — depends on cry-poly-fomc-existence)",
            required_field="rate_bound (upper/lower/midpoint/effective) used by the Polymarket rules",
            missing_or_uncertain_value="N/A until a Polymarket FOMC market is captured",
            why_it_matters="Kalshi uses an explicit bound; Polymarket sometimes uses informal language ('rate decision') that hides which bound is meant",
            blocker_it_clears="settlement_source_mismatch for Kalshi-vs-Polymarket Fed pair",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open the Polymarket Fed market discovered in eco-poly-fomc-existence.",
                "Read full rules text.",
                "Record which bound is the resolution observable.",
            ],
            screenshot_or_text_to_capture="bound-keyword excerpt from rules",
            source_url_hint="https://polymarket.com/event/<slug>",
            validation_command="none",
            repeat_cadence="per_market",
            priority="P1",
            status="missing",
        ),
        _item(
            evidence_id="eco-macro-kalshi-coverage",
            vertical="economics",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_macro_indicators",
            specific_market="CPI / payrolls / GDP markets if saved",
            ticker_url_or_report_row="(none — currently not in saved Kalshi snapshot)",
            required_field="existence-or-absence of Kalshi macro indicator markets in saved data",
            missing_or_uncertain_value="saved Kalshi snapshot is mostly crypto + FOMC; macro indicators not currently captured",
            why_it_matters="macro indicators (CPI, payrolls) are the only economics family with reliable Polymarket peers; absent saved data they cannot be reviewed",
            blocker_it_clears="no_current_peer for macro indicator family",
            exact_gate_affected="source_review",
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Run fetch-kalshi (saved-snapshot-only mode) targeted at CPI/payrolls/GDP tickers.",
                "Confirm the snapshot contains macro indicator markets.",
                "Re-run kalshi-crypto-typed-key-audit / family-graduation as appropriate.",
            ],
            screenshot_or_text_to_capture="saved snapshot diff showing macro indicator tickers",
            source_url_hint="https://kalshi.com/markets",
            validation_command="fetch-kalshi",
            repeat_cadence="one_time_per_family",
            priority="P3",
            status="missing",
        ),
        _item(
            evidence_id="eco-ibkr-fomc-queued",
            vertical="economics",
            platform="ibkr_forecastex",
            platform_status="queued",
            market_family="ibkr_forecastex_fomc",
            specific_market="ForecastEx FOMC contracts (28 final contract rows in saved data)",
            ticker_url_or_report_row="reports/ibkr_forecastex_quote_diagnostics.json",
            required_field="explicit confirmation that IBKR ForecastEx is QUEUED (not active)",
            missing_or_uncertain_value="quote diagnostics show 28 rows with bid+ask+size for 8 of them, but the integration is not approved for active use",
            why_it_matters="surfacing IBKR data as if active would lead to mis-routed liquidity assumptions; explicit queued status keeps everyone honest",
            blocker_it_clears="(none — this item exists to prevent accidental promotion to active)",
            exact_gate_affected="none",
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Read reports/ibkr_forecastex_quote_diagnostics.json + IBKR_FORECASTEX_READ_ONLY_BOUNDARY.md.",
                "Confirm IBKR is treated only as queued discovery; do not move into active.",
            ],
            screenshot_or_text_to_capture="ops-status row showing IBKR present-but-queued",
            source_url_hint="docs/IBKR_FORECASTEX_READ_ONLY_BOUNDARY.md",
            validation_command="relative-value-ops-status",
            repeat_cadence="one_time_per_family",
            priority="P4",
            status="captured_unreviewed",
            fake_edge_risk="Forgetting that IBKR is queued would let an apparent IBKR<->Kalshi Fed pair surface as active; the boundary memo prevents that mistake.",
        ),
        _item(
            evidence_id="eco-official-fomc-statement",
            vertical="economics",
            platform="official_source",
            platform_status="official_source",
            market_family="federal_reserve_statement",
            specific_market="FOMC statement / SEP / press conference",
            ticker_url_or_report_row="(none — official truth feed)",
            required_field="URL anchor for the FOMC statement page + Effective Federal Funds Rate (NY Fed) URL",
            missing_or_uncertain_value="not currently stored in repo as a named source-registry entry",
            why_it_matters="exact-review requires a named, dated official source; absent it the entire Fed lane stays at source-review tier",
            blocker_it_clears="settlement_source_missing for FOMC family",
            exact_gate_affected="source_review",
            collection_method="official_source_truth_feed",
            exact_manual_steps=[
                "Save the federalreserve.gov FOMC press-release URL pattern + the NY Fed effective-rate URL pattern as a docs/SOURCE_TAXONOMY entry.",
                "Add the anchor to docs/pending_registry_entries.",
            ],
            screenshot_or_text_to_capture="URL strings only (no scraped content)",
            source_url_hint="https://www.federalreserve.gov/newsevents/pressreleases.htm",
            validation_command="none",
            repeat_cadence="one_time_per_family",
            priority="P1",
            status="missing",
        ),
    ]


def _sports_items() -> list[dict[str, Any]]:
    return [
        _item(
            evidence_id="spt-kal-nba-championship-rules",
            vertical="sports",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_nba_championship_futures",
            specific_market="KXNBA championship market on each team",
            ticker_url_or_report_row="reports/nba_kxnba_polymarket_enriched.json",
            required_field="winner scope (NBA Finals winner only), team mapping, cancellation/void rules",
            missing_or_uncertain_value="cancellation/void rules + team mapping not captured per ticker",
            why_it_matters="Kalshi NBA championship 4 markets pair against 604 Polymarket markets in the saved sweep; without team mapping every pair is at risk of settle-on-different-team",
            blocker_it_clears="missing_typed_key:team OR settlement_source_missing for sports_futures",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open each KXNBA market page.",
                "Save rules text + team identifier explicitly.",
                "Record cancellation / void rules + finals-only scope.",
            ],
            screenshot_or_text_to_capture="rules text including team name + finals scope",
            source_url_hint="https://kalshi.com/markets/KXNBA",
            validation_command="run-nba-championship-paper-check",
            repeat_cadence="per_event",
            priority="P1",
            status="captured_unreviewed",
        ),
        _item(
            evidence_id="spt-kal-mlb-world-series-rules",
            vertical="sports",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_mlb_world_series_futures",
            specific_market="KXMLB World Series tickers",
            ticker_url_or_report_row="reports/mlb_kxmlb_kalshi_enriched.json",
            required_field="winner scope (World Series winner only) + team mapping + cancellation rules",
            missing_or_uncertain_value="30 Kalshi rows, 7 paired against 780 Polymarket rows; all 7 rejected by settlement_delta_exceeds_limit",
            why_it_matters="cross-venue settle dates differ; without aligned settlement_date the pair fails the gate even when teams match",
            blocker_it_clears="settlement_delta_exceeds_limit OR missing_typed_key:team for sports_futures",
            exact_gate_affected="exact_review",
            can_ever_affect_exact_review=True,
            collection_method="public_page",
            exact_manual_steps=[
                "Open each KXMLB World Series market.",
                "Record Kalshi end_date / settlement_date.",
                "Cross-reference Polymarket equivalent settlement_date from rules text.",
                "If they differ, mark settlement_delta_exceeds_limit as expected-not-fixable.",
            ],
            screenshot_or_text_to_capture="rules text + settlement date excerpt",
            source_url_hint="https://kalshi.com/markets/KXMLB",
            validation_command="run-mlb-world-series-paper-check",
            repeat_cadence="per_event",
            priority="P1",
            status="captured_unreviewed",
        ),
        _item(
            evidence_id="spt-kal-nhl-stanley-cup-rules",
            vertical="sports",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_nhl_stanley_cup_futures",
            specific_market="KXNHL Stanley Cup tickers",
            ticker_url_or_report_row="reports/nhl_kxnhl_polymarket_enriched.json",
            required_field="Stanley Cup winner scope + team mapping",
            missing_or_uncertain_value="4 Kalshi rows, 3 paired against 157 Polymarket rows; all WATCH",
            why_it_matters="same dynamic as MLB; team mapping + settlement_delta need explicit capture before any pair leaves WATCH",
            blocker_it_clears="settlement_delta_exceeds_limit OR missing_typed_key:team for NHL",
            exact_gate_affected="exact_review",
            can_ever_affect_exact_review=True,
            collection_method="public_page",
            exact_manual_steps=[
                "Open each KXNHL market.",
                "Record settlement_date + team identifier.",
                "Verify the equivalent Polymarket slug uses the same Cup winner scope.",
            ],
            screenshot_or_text_to_capture="rules excerpt + settlement date",
            source_url_hint="https://kalshi.com/markets/KXNHL",
            validation_command="build-nhl-stanley-cup-pairs",
            repeat_cadence="per_event",
            priority="P2",
            status="partially_captured",
        ),
        _item(
            evidence_id="spt-kal-nfl-super-bowl-existence",
            vertical="sports",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_nfl_super_bowl_futures",
            specific_market="KXNFL Super Bowl tickers",
            ticker_url_or_report_row="reports/default_sports_sweep_summary.json -> label=nfl_kxnfl, kalshi=0",
            required_field="presence of KXNFL Super Bowl markets in saved snapshot",
            missing_or_uncertain_value="zero NFL Kalshi rows currently saved (476 Polymarket rows have no Kalshi peer)",
            why_it_matters="NFL Super Bowl is the largest sports-futures lane on Polymarket; absent Kalshi data the lane is dead",
            blocker_it_clears="no_current_peer for NFL Super Bowl family",
            exact_gate_affected="source_review",
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Run fetch-kalshi targeting KXNFL.",
                "Verify Super Bowl markets are present in the saved snapshot.",
                "Re-run default_sports sweep to refresh pair counts.",
            ],
            screenshot_or_text_to_capture="saved snapshot diff showing KXNFL rows",
            source_url_hint="https://kalshi.com/markets/KXNFL",
            validation_command="fetch-kalshi",
            repeat_cadence="one_time_per_family",
            priority="P1",
            status="missing",
        ),
        _item(
            evidence_id="spt-poly-nba-token-ids",
            vertical="sports",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_nba_championship_futures",
            specific_market="each Polymarket NBA championship slug",
            ticker_url_or_report_row="reports/nba_kxnba_polymarket_enriched.json",
            required_field="clobTokenIds + rules text + team name in slug",
            missing_or_uncertain_value="token IDs already in enriched snapshot but rules text + cancellation/void rules not captured",
            why_it_matters="cross-venue settlement equivalence depends on the Polymarket cancellation rule matching Kalshi",
            blocker_it_clears="polymarket_rules_missing for NBA family",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Open each Polymarket NBA championship event page.",
                "Save full rules text including void/cancel clauses.",
                "Verify slug team name matches Kalshi.",
            ],
            screenshot_or_text_to_capture="rules text + slug",
            source_url_hint="https://polymarket.com/markets/sports/nba",
            validation_command="run-nba-championship-paper-check",
            repeat_cadence="per_event",
            priority="P1",
            status="partially_captured",
        ),
        _item(
            evidence_id="spt-poly-mlb-token-ids",
            vertical="sports",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_mlb_world_series_futures",
            specific_market="each Polymarket World Series slug",
            ticker_url_or_report_row="reports/mlb_kxmlb_polymarket_enriched.json",
            required_field="rules text + settlement_date + cancellation rules",
            missing_or_uncertain_value="MLB pair status WATCH because settlement_delta_exceeds_limit; root cause is mismatched Kalshi end_date vs Polymarket settlement",
            why_it_matters="without explicit settlement_date capture the delta gate keeps rejecting",
            blocker_it_clears="settlement_delta_exceeds_limit for MLB family",
            exact_gate_affected="exact_review",
            can_ever_affect_exact_review=True,
            collection_method="public_page",
            exact_manual_steps=[
                "Open each Polymarket World Series event page.",
                "Save rules text + the named settlement_date or 'when the World Series ends' clause.",
                "Compare against Kalshi's stored end_date.",
            ],
            screenshot_or_text_to_capture="settlement-clause excerpt",
            source_url_hint="https://polymarket.com/markets/sports/mlb",
            validation_command="run-mlb-world-series-paper-check",
            repeat_cadence="per_event",
            priority="P0",
            status="partially_captured",
        ),
        _item(
            evidence_id="spt-poly-nhl-token-ids",
            vertical="sports",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_nhl_stanley_cup_futures",
            specific_market="each Polymarket Stanley Cup slug",
            ticker_url_or_report_row="reports/nhl_kxnhl_polymarket_enriched.json",
            required_field="rules text + settlement_date + cancellation rules",
            missing_or_uncertain_value="3 paired rows are WATCH; same settlement_delta issue as MLB",
            why_it_matters="same as MLB — settlement_date alignment is the bottleneck",
            blocker_it_clears="settlement_delta_exceeds_limit for NHL family",
            exact_gate_affected="exact_review",
            can_ever_affect_exact_review=True,
            collection_method="public_page",
            exact_manual_steps=[
                "Open each Polymarket Stanley Cup event page.",
                "Capture rules + settlement clause.",
                "Diff against Kalshi end_date for the same team.",
            ],
            screenshot_or_text_to_capture="settlement-clause excerpt",
            source_url_hint="https://polymarket.com/markets/sports/nhl",
            validation_command="build-nhl-stanley-cup-pairs",
            repeat_cadence="per_event",
            priority="P1",
            status="partially_captured",
        ),
        _item(
            evidence_id="spt-poly-nfl-token-ids",
            vertical="sports",
            platform="polymarket",
            platform_status="active",
            market_family="polymarket_nfl_super_bowl_futures",
            specific_market="each Polymarket Super Bowl slug (476 rows)",
            ticker_url_or_report_row="reports/nfl_kxnfl_polymarket_enriched.json",
            required_field="rules text + cancellation/void rules + team mapping",
            missing_or_uncertain_value="476 NFL rows on Polymarket without a Kalshi peer; rules-text quality varies",
            why_it_matters="needed to graduate NFL from no_current_peer once Kalshi NFL snapshot is acquired",
            blocker_it_clears="polymarket_rules_missing for NFL family",
            exact_gate_affected="source_review",
            collection_method="public_page",
            exact_manual_steps=[
                "Spot-check a sample of Polymarket NFL Super Bowl events.",
                "Capture rules text + slug; sample is sufficient until Kalshi NFL exists.",
            ],
            screenshot_or_text_to_capture="rules text excerpts (representative sample)",
            source_url_hint="https://polymarket.com/markets/sports/nfl",
            validation_command="none",
            repeat_cadence="per_event",
            priority="P3",
            status="missing",
        ),
        _item(
            evidence_id="spt-odds-fv-reference-only",
            vertical="sports",
            platform="the_odds_api",
            platform_status="reference",
            market_family="odds_api_fair_value_reference",
            specific_market="(reference fair-value only)",
            ticker_url_or_report_row="reports/the_odds_api_fv_residuals.json (target_rows_considered=14495)",
            required_field="confirmation that Odds API is REFERENCE ONLY and never on either side of an exact pair",
            missing_or_uncertain_value="the residual report runs but matched_rows=0; Odds API only colours fair-value",
            why_it_matters="if Odds API ever surfaces as a pair side, the evaluator would have to treat it as exact-payoff which is wrong",
            blocker_it_clears="(none — guard item to prevent accidental promotion)",
            exact_gate_affected="none",
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Confirm relative-value-ops-status reports Odds API as reference-only.",
                "Confirm no scan command ever uses Odds API output as an evaluator input.",
            ],
            screenshot_or_text_to_capture="ops-status block showing the_odds_api_fv_residuals",
            source_url_hint="reports/the_odds_api_fv_residuals.json",
            validation_command="relative-value-ops-status",
            repeat_cadence="one_time_per_family",
            priority="P4",
            status="reviewed_usable",
            fake_edge_risk="Treating Odds API quotes as an exact pair side would create a structural false-positive; the residual report exists specifically to mark them reference-only.",
        ),
        _item(
            evidence_id="spt-same-payoff-board-review",
            vertical="sports",
            platform="cross_venue",
            platform_status="active",
            market_family="kalshi_polymarket_same_payoff_board",
            specific_market="MLB / NBA / NHL pair lanes",
            ticker_url_or_report_row="reports/live_readonly_match_report_with_same_payoff_evidence.json",
            required_field="manual same-payoff-board review note per pair",
            missing_or_uncertain_value="no same-payoff-board review notes stored per Kalshi-Polymarket sports pair",
            why_it_matters="exact-review requires an attested same-payoff board entry; without it no row can reach paper-review",
            blocker_it_clears="same_payoff_board_review_missing",
            exact_gate_affected="exact_review",
            can_ever_affect_exact_review=True,
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Run same-payoff-board on the latest pairs JSON.",
                "Review each row manually and record team-mapping decisions.",
                "Persist to a manual review memo (do NOT mark as reviewed_usable without explicit confirmation).",
            ],
            screenshot_or_text_to_capture="same-payoff-board review notes",
            source_url_hint="reports/live_readonly_match_report_with_same_payoff_evidence.json",
            validation_command="same-payoff-board",
            repeat_cadence="per_event",
            priority="P1",
            status="missing",
        ),
        _item(
            evidence_id="spt-fee-evidence-per-family",
            vertical="sports",
            platform="kalshi",
            platform_status="active",
            market_family="kalshi_sports_fee_schedule",
            specific_market="all sports markets",
            ticker_url_or_report_row="docs/CODEX_HANDOFF.md (fee evidence section)",
            required_field="effective per-side fee per sport category",
            missing_or_uncertain_value="fee schedule per sport category not stored as a JSON memo",
            why_it_matters="net-gap evaluator subtracts both sides' fees; mis-recording sport fees breaks paper-review at the right magnitude",
            blocker_it_clears="missing_fee_metadata for sports rows",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="logged_in_ui",
            exact_manual_steps=[
                "Open order entry on representative MLB/NBA/NHL markets (no submission).",
                "Read maker + taker + settlement fees for each.",
                "Save to a single per-category fee memo.",
            ],
            screenshot_or_text_to_capture="fee-preview screenshot per category",
            source_url_hint="https://kalshi.com/about/fees",
            validation_command="none",
            repeat_cadence="per_fee_schedule_change",
            priority="P2",
            status="missing",
        ),
    ]


def _cross_cutting_items() -> list[dict[str, Any]]:
    return [
        _item(
            evidence_id="x-source-taxonomy-anchor-urls",
            vertical="cross_cutting",
            platform="official_source",
            platform_status="official_source",
            market_family="source_taxonomy_anchor_urls",
            specific_market=None,
            ticker_url_or_report_row="docs/SOURCE_TAXONOMY.md",
            required_field="named, dated official-source URL per family (FOMC, BRTI, ERTI, MLB, NBA, NHL, NFL)",
            missing_or_uncertain_value="docs/SOURCE_TAXONOMY.md exists but is not enumerated as a per-family table",
            why_it_matters="exact-review gate references named sources; the registry needs a single authoritative anchor URL per family",
            blocker_it_clears="settlement_source_missing across multiple families",
            exact_gate_affected="exact_review",
            can_ever_affect_exact_review=True,
            collection_method="official_source_truth_feed",
            exact_manual_steps=[
                "Read docs/SOURCE_TAXONOMY.md.",
                "For every family that lacks an anchor URL, add a row to docs/pending_registry_entries/.",
                "Manually validate the URL is still live.",
            ],
            screenshot_or_text_to_capture="updated docs/SOURCE_TAXONOMY.md table",
            source_url_hint="docs/SOURCE_TAXONOMY.md",
            validation_command="canonical-registry-coverage",
            repeat_cadence="one_time_per_family",
            priority="P1",
            status="partially_captured",
        ),
        _item(
            evidence_id="x-fee-evidence-registry",
            vertical="cross_cutting",
            platform="kalshi",
            platform_status="active",
            market_family="fee_evidence_registry",
            specific_market=None,
            ticker_url_or_report_row="docs/CODEX_HANDOFF.md (fee evidence section)",
            required_field="single per-category fee JSON memo so evaluator can subtract a verified fee",
            missing_or_uncertain_value="fees are documented in markdown but not captured as a structured per-category memo",
            why_it_matters="paper-review threshold relies on a verified fee number; markdown text is not parsable",
            blocker_it_clears="missing_fee_metadata cross-family",
            exact_gate_affected="paper_review",
            can_ever_affect_paper_review=True,
            collection_method="logged_in_ui",
            exact_manual_steps=[
                "Open order preview (no submission) on a sample market per category.",
                "Save a single fee memo JSON.",
                "Reference it from evaluator config.",
            ],
            screenshot_or_text_to_capture="per-category fee values",
            source_url_hint="https://kalshi.com/about/fees",
            validation_command="none",
            repeat_cadence="per_fee_schedule_change",
            priority="P1",
            status="missing",
        ),
        _item(
            evidence_id="x-evidence-burden-review-cycle",
            vertical="cross_cutting",
            platform="cross_venue",
            platform_status="active",
            market_family="evidence_burden_review_cycle",
            specific_market=None,
            ticker_url_or_report_row="reports/settlement_evidence_burden.json (if present)",
            required_field="weekly cadence for re-running the saved-file diagnostic sweep",
            missing_or_uncertain_value="no scheduled cadence; reports drift weekly without a re-run",
            why_it_matters="without scheduled re-runs the audit blockers go stale; settled rows linger in the queue and skew priority signals",
            blocker_it_clears="(meta — operational hygiene)",
            exact_gate_affected="none",
            collection_method="api_or_saved_file",
            exact_manual_steps=[
                "Document a per-week cadence: refresh Kalshi snapshot, refresh CDNA fixtures, refresh Polymarket discovery, then re-run relative-value-ops-status.",
                "Capture the cadence into docs/CURRENT_STATUS.md.",
            ],
            screenshot_or_text_to_capture="cadence checklist in docs",
            source_url_hint="docs/CURRENT_STATUS.md",
            validation_command="relative-value-ops-status",
            repeat_cadence="one_time_per_family",
            priority="P2",
            status="partially_captured",
        ),
    ]


_CATALOGUE_BUILDERS = (_crypto_items, _economics_items, _sports_items, _cross_cutting_items)


# ---------------------------------------------------------------------------
# Build + write
# ---------------------------------------------------------------------------


def build_manual_evidence_requirements_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")

    warnings: list[dict[str, Any]] = []
    inputs_present: dict[str, bool] = {}
    for key, filename in _INPUT_FILES.items():
        path = input_dir / filename
        inputs_present[key] = path.exists()
        if not path.exists():
            warnings.append(
                {
                    "source_file": str(path),
                    "reason_code": f"{key}_missing",
                    "blocker": f"{key}_missing",
                }
            )

    items: list[dict[str, Any]] = []
    for builder in _CATALOGUE_BUILDERS:
        for item in builder():
            items.append(_normalize_item(copy.deepcopy(item)))

    items.sort(key=_item_sort_key)

    summary = _summary(items)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "diagnostic_only": True,
        "saved_files_only": True,
        "inputs_present": inputs_present,
        "verticals": ["crypto", "economics", "sports", "cross_cutting"],
        "priorities": list(_VALID_PRIORITIES),
        "summary": summary,
        "items": items,
        "warnings": warnings,
        "safety": _safety_block(),
    }


def write_manual_evidence_requirements_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_manual_evidence_requirements_report(
        input_dir=input_dir, generated_at=generated_at
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_manual_evidence_requirements_markdown(report), encoding="utf-8")
    return report


def render_manual_evidence_requirements_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    items = report.get("items") or []
    lines: list[str] = [
        "# Manual Evidence Requirements",
        "",
        "Saved-file-only catalogue of every manual evidence item Mason needs to collect to "
        "move Crypto / Economics / Sports rows from missing-evidence into source-review or "
        "manual-review. Diagnostic only: no item clears evaluator gates on its own; no item "
        "creates a paper candidate; manual capture only moves rows along the source-review "
        "track until strict relative-value gates pass.",
        "",
        "## Executive Summary",
        "",
        f"- total_items: `{summary.get('total_items', 0)}`",
        f"- verticals: `{','.join(summary.get('verticals') or [])}`",
        f"- platform_status_active: `{(summary.get('platform_status_counts') or {}).get('active', 0)}`",
        f"- platform_status_queued: `{(summary.get('platform_status_counts') or {}).get('queued', 0)}`",
        f"- platform_status_reference: `{(summary.get('platform_status_counts') or {}).get('reference', 0)}`",
        f"- platform_status_official_source: `{(summary.get('platform_status_counts') or {}).get('official_source', 0)}`",
        "",
        "### Priority Counts",
        "",
        "| Priority | Count |",
        "|---|---:|",
    ]
    for prio in _VALID_PRIORITIES:
        lines.append(f"| {prio} | {(summary.get('priority_counts') or {}).get(prio, 0)} |")
    lines.extend(["", "### Status Counts", "", "| Status | Count |", "|---|---:|"])
    for status, count in sorted((summary.get("status_counts") or {}).items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "### Items by Vertical × Platform", "", "| Vertical | Platform | Status | Count |", "|---|---|---|---:|"])
    for entry in summary.get("counts_by_vertical_platform_status") or []:
        lines.append(
            f"| {entry['vertical']} | {entry['platform']} | {entry['platform_status']} | {entry['count']} |"
        )
    lines.extend(
        [
            "",
            "## Top 10 Manual Actions This Week",
            "",
            "| # | ID | Priority | Vertical | Platform | What to do |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for i, item in enumerate(summary.get("top_10_this_week") or [], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    item["evidence_id"],
                    item["priority"],
                    item["vertical"],
                    item["platform"],
                    _md(item.get("required_field")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Top 10 Manual Actions That Unlock The Most Rows",
            "",
            "| # | ID | Family | Estimated Rows Unlocked | Blocker Cleared |",
            "|---:|---|---|---:|---|",
        ]
    )
    for i, item in enumerate(summary.get("top_10_unlock_most_rows") or [], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    item["evidence_id"],
                    item["market_family"],
                    str(item.get("estimated_rows_unlocked", 0)),
                    _md(item.get("blocker_it_clears")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Top 10 Manual Actions That Reduce Fake-Edge Risk",
            "",
            "| # | ID | Family | Fake-Edge Risk |",
            "|---:|---|---|---|",
        ]
    )
    for i, item in enumerate(summary.get("top_10_reduce_fake_edge_risk") or [], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    item["evidence_id"],
                    item["market_family"],
                    _md(item.get("fake_edge_risk")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Top 10 One-Time Reusable Evidence",
            "",
            "| # | ID | Family | Repeat Cadence |",
            "|---:|---|---|---|",
        ]
    )
    for i, item in enumerate(summary.get("top_10_one_time_reusable") or [], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    item["evidence_id"],
                    item["market_family"],
                    item["repeat_cadence"],
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Manual Actions Not Worth Doing Yet",
            "",
            "| ID | Family | Reason |",
            "|---|---|---|",
        ]
    )
    for item in summary.get("not_worth_doing_yet") or []:
        lines.append(
            f"| {item['evidence_id']} | {item['market_family']} | {_md(item.get('reason'))} |"
        )
    lines.extend(
        [
            "",
            "## Do Once Checklist",
            "",
        ]
    )
    for item in summary.get("do_once_checklist") or []:
        lines.append(f"- [ ] `{item['evidence_id']}` — {_md(item.get('required_field'))}")
    lines.extend(["", "## Do Per Market Family Checklist", ""])
    for item in summary.get("do_per_market_family_checklist") or []:
        lines.append(f"- [ ] `{item['evidence_id']}` — {_md(item.get('required_field'))}")
    lines.extend(["", "## Do Per Specific Market Checklist", ""])
    for item in summary.get("do_per_specific_market_checklist") or []:
        lines.append(f"- [ ] `{item['evidence_id']}` — {_md(item.get('required_field'))}")
    lines.extend(["", "## Do Before Any Paper Review Checklist", ""])
    for item in summary.get("do_before_paper_review_checklist") or []:
        lines.append(f"- [ ] `{item['evidence_id']}` — {_md(item.get('required_field'))}")
    lines.extend(
        [
            "",
            "## Full Item Catalogue",
            "",
        ]
    )
    for item in items:
        lines.append(f"### {item['evidence_id']}  (`{item['priority']}` / `{item['status']}`)")
        lines.append("")
        lines.append(f"- vertical: `{item['vertical']}`")
        lines.append(f"- platform: `{item['platform']}` (`{item['platform_status']}`)")
        lines.append(f"- market_family: `{item['market_family']}`")
        lines.append(f"- specific_market: `{_md(item.get('specific_market'))}`")
        lines.append(f"- ticker_url_or_report_row: `{_md(item.get('ticker_url_or_report_row'))}`")
        lines.append(f"- required_field: `{_md(item.get('required_field'))}`")
        lines.append(f"- missing_or_uncertain_value: {_md(item.get('missing_or_uncertain_value'))}")
        lines.append(f"- why_it_matters: {_md(item.get('why_it_matters'))}")
        lines.append(f"- blocker_it_clears: `{_md(item.get('blocker_it_clears'))}`")
        lines.append(f"- exact_gate_affected: `{item.get('exact_gate_affected')}`")
        lines.append(f"- can_ever_affect_exact_review: `{str(bool(item.get('can_ever_affect_exact_review'))).lower()}`")
        lines.append(f"- can_ever_affect_paper_review: `{str(bool(item.get('can_ever_affect_paper_review'))).lower()}`")
        lines.append(f"- collection_method: `{item.get('collection_method')}`")
        lines.append(f"- repeat_cadence: `{item.get('repeat_cadence')}`")
        lines.append(f"- validation_command: `{item.get('validation_command')}`")
        lines.append("- exact_manual_steps:")
        for step in item.get("exact_manual_steps") or []:
            lines.append(f"  - {step}")
        lines.append(f"- screenshot_or_text_to_capture: {_md(item.get('screenshot_or_text_to_capture'))}")
        lines.append(f"- source_url_hint: `{_md(item.get('source_url_hint'))}`")
        lines.append(f"- fake_edge_risk: {_md(item.get('fake_edge_risk'))}")
        lines.append("")
    lines.extend(
        [
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- saved_files_only: `true`",
            "- no_manual_evidence_clears_evaluator_gates_on_its_own: `true`",
            "- no_manual_evidence_creates_paper_candidate: `true`",
            "- queued_platforms_remain_queued: `true`",
            "- reference_only_platforms_never_become_pair_side: `true`",
            "- exact_ready_rows: `0`",
            "- paper_candidate_rows: `0`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Validation, normalization, summary
# ---------------------------------------------------------------------------


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    _require_in(item, "evidence_id", str, allow_empty=False)
    _require_in(item, "vertical", str, allowed=_VALID_VERTICALS)
    _require_in(item, "platform", str, allowed=_VALID_PLATFORMS)
    _require_in(item, "platform_status", str, allowed=_VALID_PLATFORM_STATUSES)
    _require_in(item, "market_family", str, allow_empty=False)
    _require_in(item, "required_field", str, allow_empty=False)
    _require_in(item, "missing_or_uncertain_value", str, allow_empty=False)
    _require_in(item, "why_it_matters", str, allow_empty=False)
    _require_in(item, "blocker_it_clears", str, allow_empty=False)
    _require_in(item, "exact_gate_affected", str, allowed=_VALID_GATES)
    _require_in(item, "collection_method", str, allowed=_VALID_COLLECTION_METHODS)
    _require_in(item, "repeat_cadence", str, allowed=_VALID_REPEAT_CADENCES)
    _require_in(item, "priority", str, allowed=set(_VALID_PRIORITIES))
    _require_in(item, "status", str, allowed=_VALID_STATUSES)
    _require_in(item, "fake_edge_risk", str, allow_empty=False)
    steps = item.get("exact_manual_steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{item.get('evidence_id')}: exact_manual_steps must be a non-empty list")
    # Safety flags — keep at false everywhere.
    item.setdefault("diagnostic_only", True)
    item.setdefault("can_create_candidate_pair", False)
    item.setdefault("can_create_paper_candidate", False)
    item.setdefault("paper_candidate", False)
    item.setdefault("exact_ready", False)
    item.setdefault("execution_ready", False)
    item.setdefault("clears_evaluator_gate_alone", False)
    if item.get("paper_candidate") is True:
        raise ValueError(f"{item['evidence_id']}: paper_candidate must remain false")
    if item.get("exact_ready") is True:
        raise ValueError(f"{item['evidence_id']}: exact_ready must remain false")
    if item.get("can_create_paper_candidate") is True:
        raise ValueError(f"{item['evidence_id']}: can_create_paper_candidate must remain false")
    if item.get("can_create_candidate_pair") is True:
        raise ValueError(f"{item['evidence_id']}: can_create_candidate_pair must remain false")
    # Queued platforms can never have status=reviewed_usable in this catalogue.
    if item["platform_status"] == "queued" and item["status"] == "reviewed_usable":
        raise ValueError(f"{item['evidence_id']}: queued platforms cannot be reviewed_usable yet")
    return item


def _require_in(
    item: dict[str, Any],
    key: str,
    expected_type: type,
    *,
    allowed: set[str] | frozenset[str] | None = None,
    allow_empty: bool = True,
) -> None:
    value = item.get(key)
    if not isinstance(value, expected_type):
        raise ValueError(f"{item.get('evidence_id', '<unknown>')}: {key} must be {expected_type.__name__}")
    if not allow_empty and expected_type is str and not value.strip():
        raise ValueError(f"{item.get('evidence_id', '<unknown>')}: {key} must be non-empty")
    if allowed is not None and value not in allowed:
        raise ValueError(
            f"{item.get('evidence_id', '<unknown>')}: {key}={value!r} not in {sorted(allowed)}"
        )


def _item_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    return (
        _VALID_PRIORITIES.index(item["priority"]),
        item["vertical"],
        item["evidence_id"],
    )


# Rough "row-unlock" estimates derived from saved-report blocker counts. Pure
# heuristics — they exist to rank manual work, not to claim a real budget.
_ROW_UNLOCK_ESTIMATES: dict[str, int] = {
    "cry-kal-fresh-orderbook": 2516,
    "cry-poly-clob-token-ids": 463,
    "cry-poly-hit-touch-rules-text": 333,
    "cry-poly-point-in-time-existence": 239,
    "cry-poly-up-down-rules": 21,
    "cry-cdna-eth-pit-fixture": 6,
    "cry-cdna-btc-pit-fixture": 6,
    "eco-kal-fomc-fresh-quote": 228,
    "eco-kal-fomc-rate-bound-definition": 10,
    "eco-kal-fomc-comparator-strictness": 10,
    "eco-poly-fomc-existence": 1,
    "spt-poly-mlb-token-ids": 7,
    "spt-poly-nhl-token-ids": 3,
    "spt-kal-nfl-super-bowl-existence": 476,
    "spt-poly-nfl-token-ids": 476,
    "spt-same-payoff-board-review": 14,
}


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    priority_counts: Counter[str] = Counter(item["priority"] for item in items)
    status_counts: Counter[str] = Counter(item["status"] for item in items)
    vertical_counts: Counter[str] = Counter(item["vertical"] for item in items)
    platform_counts: Counter[str] = Counter(item["platform"] for item in items)
    platform_status_counts: Counter[str] = Counter(item["platform_status"] for item in items)
    counts_by_vertical_platform_status: list[dict[str, Any]] = []
    nested: Counter[tuple[str, str, str]] = Counter()
    for item in items:
        nested[(item["vertical"], item["platform"], item["platform_status"])] += 1
    for (vertical, platform, status), count in sorted(nested.items()):
        counts_by_vertical_platform_status.append(
            {"vertical": vertical, "platform": platform, "platform_status": status, "count": count}
        )
    for item in items:
        item["estimated_rows_unlocked"] = _ROW_UNLOCK_ESTIMATES.get(item["evidence_id"], 0)

    top_10_this_week = [
        _short_item(item)
        for item in sorted(
            items,
            key=lambda i: (
                _VALID_PRIORITIES.index(i["priority"]),
                -i["estimated_rows_unlocked"],
                i["evidence_id"],
            ),
        )[:10]
    ]
    top_10_unlock = [
        _short_item(item)
        for item in sorted(
            items,
            key=lambda i: (-(i["estimated_rows_unlocked"]), _VALID_PRIORITIES.index(i["priority"])),
        )[:10]
    ]
    top_10_fake_edge = [
        _short_item(item)
        for item in sorted(items, key=lambda i: -_fake_edge_severity(i))[:10]
    ]
    top_10_one_time = [
        _short_item(item)
        for item in items
        if item.get("repeat_cadence") == "one_time_per_family"
    ][:10]
    not_worth = [
        {**_short_item(item), "reason": _not_worth_reason(item)}
        for item in items
        if _not_worth_reason(item) is not None
    ]
    do_once = [_short_item(item) for item in items if item.get("repeat_cadence") == "one_time_per_family"]
    do_per_family = [_short_item(item) for item in items if item.get("repeat_cadence") in {"per_rules_version", "per_fee_schedule_change"}]
    do_per_market = [_short_item(item) for item in items if item.get("repeat_cadence") in {"per_market", "per_event", "per_contract_month"}]
    do_before_paper = [
        _short_item(item)
        for item in items
        if item.get("can_ever_affect_paper_review")
        and item.get("status") not in {"reviewed_usable", "validated_diagnostic"}
    ]

    # Vertical position assessments.
    closest_to_source_review, closest_to_exact_review, distraction = _vertical_progress(items)

    return {
        "total_items": len(items),
        "verticals": sorted(vertical_counts.keys()),
        "vertical_counts": dict(vertical_counts),
        "platform_counts": dict(platform_counts),
        "platform_status_counts": dict(platform_status_counts),
        "priority_counts": dict(priority_counts),
        "status_counts": dict(status_counts),
        "counts_by_vertical_platform_status": counts_by_vertical_platform_status,
        "top_10_this_week": top_10_this_week,
        "top_10_unlock_most_rows": top_10_unlock,
        "top_10_reduce_fake_edge_risk": top_10_fake_edge,
        "top_10_one_time_reusable": top_10_one_time,
        "not_worth_doing_yet": not_worth,
        "do_once_checklist": do_once,
        "do_per_market_family_checklist": do_per_family,
        "do_per_specific_market_checklist": do_per_market,
        "do_before_paper_review_checklist": do_before_paper,
        "closest_to_source_review_vertical": closest_to_source_review,
        "closest_to_exact_review_vertical": closest_to_exact_review,
        "distraction_vertical": distraction,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "no_manual_evidence_clears_evaluator_gates_on_its_own": True,
        "no_manual_evidence_creates_paper_candidate": True,
        "queued_platforms_remain_queued": all(
            item["status"] != "reviewed_usable"
            for item in items
            if item["platform_status"] == "queued"
        ),
        "reference_only_platforms_never_become_pair_side": all(
            not item.get("can_ever_affect_exact_review")
            for item in items
            if item["platform_status"] == "reference"
        ),
    }


def _short_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item["evidence_id"],
        "priority": item["priority"],
        "vertical": item["vertical"],
        "platform": item["platform"],
        "platform_status": item["platform_status"],
        "market_family": item["market_family"],
        "required_field": item.get("required_field"),
        "blocker_it_clears": item.get("blocker_it_clears"),
        "repeat_cadence": item.get("repeat_cadence"),
        "exact_gate_affected": item.get("exact_gate_affected"),
        "estimated_rows_unlocked": item.get("estimated_rows_unlocked", 0),
        "fake_edge_risk": item.get("fake_edge_risk"),
        "status": item.get("status"),
    }


def _fake_edge_severity(item: dict[str, Any]) -> int:
    # Heuristic severity: items that touch comparator / source / bound / inclusivity
    # / cancellation rules carry the highest false-positive arb risk.
    body = (item.get("fake_edge_risk") or "").lower()
    score = 0
    if "false-positive" in body or "false positive" in body:
        score += 5
    if "comparator" in body or "bound" in body or "inclusivity" in body:
        score += 3
    if "source" in body or "index" in body or "settlement" in body:
        score += 2
    if "open" in body and "close" in body:
        score += 2
    if "cancellation" in body or "void" in body:
        score += 2
    if item.get("priority") in {"P0", "P1"}:
        score += 1
    return score


def _not_worth_reason(item: dict[str, Any]) -> str | None:
    # P4 + status=reviewed_usable items are guard items that exist only to prevent
    # accidental promotion; they are not "actions" Mason should chase.
    if item.get("priority") == "P4" and item.get("status") == "reviewed_usable":
        return "Guard item — already in place; no action needed."
    if item.get("priority") == "P4":
        return "Lowest-priority detail; revisit only after P0–P2 manual evidence is collected."
    if item["platform_status"] == "queued":
        return "Platform is queued; do not invest active manual effort yet."
    return None


def _vertical_progress(items: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Pick a vertical for each of three positional roles.

    - **closest_to_source_review** — the vertical with the most *imminent* P0
      unlock items per total catalogue size; a small batch of high-priority
      missing items there pushes the most rows into source-review.
    - **closest_to_exact_review** — the vertical that already has the largest
      count of ``can_ever_affect_exact_review`` items; this is where the next
      tier (exact-review) is structurally reachable.
    - **distraction** — the vertical where ``can_ever_affect_exact_review``
      is zero AND the catalogue carries many items; lots of manual work,
      no path to exact-review without a structural change.
    """
    by_vertical: dict[str, dict[str, int]] = {}
    for item in items:
        vertical = item["vertical"]
        bucket = by_vertical.setdefault(
            vertical,
            {
                "total": 0,
                "p0": 0,
                "p1": 0,
                "p0_missing": 0,
                "exact_capable": 0,
                "partial": 0,
                "captured": 0,
                "missing": 0,
            },
        )
        bucket["total"] += 1
        if item["priority"] == "P0":
            bucket["p0"] += 1
            if item["status"] in {"missing", "partially_captured"}:
                bucket["p0_missing"] += 1
        if item["priority"] == "P1":
            bucket["p1"] += 1
        if item.get("can_ever_affect_exact_review"):
            bucket["exact_capable"] += 1
        if item["status"] == "partially_captured":
            bucket["partial"] += 1
        if item["status"] == "captured_unreviewed":
            bucket["captured"] += 1
        if item["status"] == "missing":
            bucket["missing"] += 1
    candidates = {
        v: by_vertical.get(v, {}) for v in ("crypto", "economics", "sports") if v in by_vertical
    }

    def _p0_density(stats: dict[str, int]) -> float:
        total = stats.get("total") or 0
        return (stats.get("p0_missing") or 0) / total if total else 0.0

    # Closest to source-review = highest density of P0 imminent-unlock items.
    closest_to_source_review = max(
        candidates,
        key=lambda v: (
            _p0_density(candidates[v]),
            candidates[v].get("p0", 0) + candidates[v].get("p1", 0),
        ),
        default="economics",
    )
    # Closest to exact-review = vertical with the most can_ever_affect_exact_review items.
    closest_to_exact_review = max(
        candidates,
        key=lambda v: (
            candidates[v].get("exact_capable", 0),
            candidates[v].get("partial", 0) + candidates[v].get("captured", 0),
        ),
        default="sports",
    )
    # Distraction = vertical with zero exact-capable items AND most total items.
    distraction_pool = [
        v for v in candidates if candidates[v].get("exact_capable", 0) == 0
    ]
    if distraction_pool:
        distraction = max(distraction_pool, key=lambda v: candidates[v].get("total", 0))
    else:
        distraction = min(
            candidates,
            key=lambda v: candidates[v].get("exact_capable", 0),
            default="crypto",
        )
    # Prevent the same vertical from filling two slots — that signal is muddled.
    if distraction == closest_to_exact_review and len(candidates) >= 2:
        alt = [v for v in candidates if v != closest_to_exact_review]
        if alt:
            distraction = max(alt, key=lambda v: candidates[v].get("total", 0))
    return closest_to_source_review, closest_to_exact_review, distraction


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "exact_ready": False,
        "execution_ready": False,
        "paper_candidate": False,
        "affects_evaluator_gates": False,
        "source_registry_unchanged": True,
        "no_manual_evidence_clears_evaluator_gates_on_its_own": True,
        "no_manual_evidence_creates_paper_candidate": True,
        "queued_platforms_remain_queued": True,
        "reference_only_platforms_never_become_pair_side": True,
    }


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "/").replace("\n", " ")
