# IBKR / ForecastEx FF Manual UI Memo

This memo is a diagnostic-only scaffold for manually recording ForecastEx FF market rules, settlement, fee, and market-data observations from the IBKR UI.

Do not use this memo to clear `settlement_rules_need_review`, set execution readiness, create same-payoff pairs, or emit `PAPER_CANDIDATE`. It is evidence collection only.

## Manual Capture

Fill a copy of `docs/templates/ibkr_forecastex_ff_manual_ui_memo.template.json` from the IBKR UI after manual login.

Required fields:

- `market_rules_full_text`
- `about_this_market_full_text`
- `exchange_rules_full_text`
- `expiration_and_last_trading_time`
- `settlement_source_name`
- `settlement_source_url`
- `settlement_source_field`
- `threshold_semantics`
- `comparator_semantics`
- `settlement_event_date`
- `fomc_meeting_date`
- `sample_strikes`
- `commission_schedule_observed`
- `order_preview_fee_observation`
- `marketdata_permission_status_observed`
- `realtime_or_delayed_observed`
- `void_cancellation_rules_text`
- `reviewer_name_or_initials`
- `reviewed_at`
- `source_ui_surface`
- `ibkr_ui_capture_status`
- `applies_to_other_months`
- `contract_symbol_or_id_reviewed`
- `ibkr_forecastx_month_reviewed`
- `api_month_currently_fetched`

Allowed `threshold_semantics` values:

- `upper_bound`
- `lower_bound`
- `midpoint`
- `effective_rate`
- `unknown`

Allowed `comparator_semantics` values:

- `above`
- `at_or_above`
- `greater_than`
- `unknown`

Allowed `ibkr_ui_capture_status` values:

- `captured`
- `partially_captured`
- `not_captured`

Allowed `applies_to_other_months` values:

- `yes_verified`
- `no`
- `unknown_without_separate_review`

## Validation

Run:

```powershell
python scan.py validate-ibkr-forecastex-manual-memo --memo-json path\to\filled_memo.json
```

The validator reports missing, unknown, invalid, and caveated fields. `validation_passed=true` only means the memo has no validator blockers. It does not mean the memo can be merged into normalized rows.

The following caveats are blockers:

- IBKR UI not captured.
- IBKR UI only partially captured.
- Reviewed month does not match the currently fetched API month.
- Applicability to other months is unknown.
- Memo explicitly does not apply to other months.
- Threshold or comparator semantics are `unknown`.
- Settlement source or fee observations are missing.

`memo_credibility_for_downstream_merge` is false unless all blockers are cleared, IBKR UI capture or an explicitly accepted reviewed alternative exists, month coverage is proven, and threshold/comparator/settlement/fee evidence is non-unknown.

Even a passing memo does not clear `settlement_rules_need_review`, does not create pair eligibility, does not change source readiness, and does not alter evaluator gates.

## Current Boundary

IBKR unified UI can show Kalshi/CME/ForecastEx; `exchange_venue`, not tab/source platform alone, determines independence.

ForecastEx FF remains diagnostic-only until rules, fees, settlement semantics, quote freshness, and market-data permissions are reviewed explicitly.
