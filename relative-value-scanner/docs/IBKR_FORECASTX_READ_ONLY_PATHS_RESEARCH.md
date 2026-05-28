# IBKR ForecastEx Read-Only Path Research

Date: 2026-05-26

This memo reviews locally installed IBKR Client Portal Gateway documentation and repo-local evidence for read-only paths that could enumerate final ForecastEx / FORECASTX event-contract rows for an underlier such as `FF` / US Fed Funds Target Rate / conid `658663572`.

No live API calls were made. No endpoints were added to the fetcher allow-list. Online documentation was not used.

## Local Sources Searched

- `C:\Users\mason\Downloads\clientportal.gw\doc\GettingStarted.md`
- `C:\Users\mason\Downloads\clientportal.gw\doc\RealtimeSubscription.md`
- `C:\Users\mason\Downloads\clientportal.gw\root\webapps\demo\gateway.demo.js`
- `C:\Users\mason\Downloads\clientportal.gw\dist\ibgroup.web.core.iblink.router.clientportal.gw.jar`
  - `swagger/iblink.router.iserver.swagger.yaml`
  - `swagger/iblink.router.calendar.swagger.yaml`
  - `swagger/iblink.router.fyi.swagger.yaml`
- Repo-local IBKR / ForecastEx files:
  - `relative_value/ibkr_forecastex_readonly_access.py`
  - `relative_value/ibkr_forecastex_read_only_boundary.py`
  - `docs/IBKR_FORECASTEX_READ_ONLY_BOUNDARY.md`
  - `tests/test_ibkr_forecastex_readonly_access.py`
  - `reports/ibkr_forecastex_discovery_candidates.json`
  - `reports/ibkr_forecastex_normalized_draft.json`
  - `reports/ibkr_forecastex_raw_shape_summary.json`

Search terms included `FORECASTX`, `ForecastEx`, `Forecast Contract`, `Event Contract`, `secdef`, `strikes`, `trsrv`, `contract`, `info-and-rules`, `algos`, `orders`, `portfolio`, `positions`, and `account`.

## Executive Finding

The local gateway documentation does not prove a concrete read-only endpoint sequence that enumerates final tradable ForecastEx Yes/No or C/P event-contract rows from the `FF` underlier.

The local docs do prove or reference a small set of read-only-ish discovery and market-data concepts:

- `/iserver/secdef/search` is referenced for finding stock conids.
- `/trsrv/futures` is referenced for finding futures conids.
- `/iserver/marketdata/snapshot` is referenced for top-of-book market data after a conid is already known.
- WebSocket market data topic `smd+conid` is documented, but it requires a final conid and does not enumerate contracts.

The same local docs and demo JS also document account, order, portfolio, position, and PnL paths that are tainted for this project and must not be used.

The saved raw ForecastEx evidence remains underlier-only: `ForecastX` identifiers are present, but no final C/P right, binary Yes/No, strike/event threshold, or expiry/month fields are present in a way that proves a final tradable contract row.

## Candidate Path Review

| Candidate path | Documented locally | Read-only | Account/order/portfolio/position/balance taint | Parameter signature | Required identifiers | Expected returned fields | Can prove final ForecastEx Yes/No or C/P contract fields | Account-specific permissions | Safety concerns |
|---|---:|---:|---:|---|---|---|---:|---|---|
| `/iserver/secdef/search` | Yes, referenced in `RealtimeSubscription.md` for stock conid lookup | Yes/likely | No taint in path or local text | Local docs only state it can be used to find a stock conid. Repo uses `symbol=<term>`. | Search symbol such as `FF` | Local docs do not define schema. Saved raw responses contain underlier-style fields such as conid, symbol, description/companyHeader, sections. | No. It can identify a ForecastEx underlier, but saved raw evidence did not contain final C/P or Yes/No contract rows. | Authenticated local gateway required; instrument entitlement unclear. | Safe as a read-only discovery endpoint, but insufficient by itself. |
| `/trsrv/futures` | Yes, referenced in `RealtimeSubscription.md` for futures conid lookup | Yes/likely | No taint in path or local text | Not specified locally | Futures symbol | Not specified locally | No proof. It is documented for futures, not ForecastEx event contracts. | Authenticated local gateway likely required; entitlement unclear. | Not a proven ForecastEx event-contract path. |
| `/iserver/marketdata/snapshot` | Yes, referenced in `RealtimeSubscription.md`; demo JS builds `conids`, `since`, and optional `fields` query | Yes/market-data read | No account/order taint in path, but market-data permissions may apply | `conids=<id>&since=<value>&fields=<fields>` per demo JS | Final conid | Top-of-book fields for a known conid | No. It requires final conids and cannot enumerate them. | Market-data permission may be required. | Safe only after final conids are known. |
| WebSocket `smd+conid` | Yes, documented in `RealtimeSubscription.md` | Yes/market-data read | Same WebSocket doc also covers live orders and PnL topics, so topic selection matters | `smd+<conid>+{"fields":[...]}` | Final conid | Streaming market data fields | No. It requires final conids and cannot enumerate them. | Market-data permission may be required. | Not needed for this saved-file workflow; avoid live streaming in this lane. |
| `/iserver/secdef/strikes` with `secType=EC` and `exchange=FORECASTX` | No local documentation found | Unclear, likely security-definition if endpoint is valid | No direct path taint, but not locally proven | Current repo pattern expects underlier conid, `secType`, month, and exchange; local docs do not define this endpoint | Underlier conid, month, secType, exchange | Not locally documented. External/options-style expectation would be strikes/months/rights, but this is not proven locally. | Unproven. It could be part of the missing options-style workflow, but local docs did not establish it. | Unclear. | Do not expand use based on this memo alone. Missing local proof and missing month/right parameters remain blockers. |
| `/iserver/secdef/info` | No local documentation found | Unclear, likely security-definition if endpoint is valid | No direct path taint, but not locally proven | Current repo pattern uses conid and optional secdef parameters; local docs do not define this endpoint | Conid and possibly month, strike, right, secType, exchange | Saved raw responses are dict-shaped and underlier-like for ForecastEx examples | No. Saved responses did not prove final C/P or Yes/No rows. | Unclear. | Existing use should remain fail-closed; do not infer tradability from underlier-only rows. |
| `/trsrv/secdef` | No local documentation found | Unclear | Unclear/no direct account/order words, but no local proof | Unknown | Unknown | Unknown | Unknown. Local docs do not prove it exists or is appropriate. | Unclear. | Not suitable for allow-list expansion in this task. |
| `/iserver/contract/{conid}/info-and-rules` | No local documentation found | Unclear | Unclear/no direct account/order words, but no local proof | Unknown beyond `{conid}` | Conid | Unknown | Unknown. Local docs do not prove it exists or returns final event-contract fields. | Unclear. | Not suitable for allow-list expansion in this task. |
| `/iserver/contract/{conid}/algos` | No local documentation found | Unclear, order-algo adjacent | Order/execution-adjacent concern | Unknown beyond `{conid}` | Conid | Unknown | No evidence it enumerates contracts | Unclear | Avoid. The name is order-algorithm adjacent and not relevant to read-only contract enumeration. |
| `/iserver/accounts` | Yes, local bundled Swagger documents it | No for this project lane | Account taint: yes | None documented beyond authenticated request | Authenticated session | Brokerage account ids | No | Account access required | Forbidden by project constraints. |
| `/portfolio/accounts` | Yes, demo JS calls it | No for this project lane | Account/portfolio taint: yes | None shown in demo call | Authenticated session | Accounts | No | Account access required | Forbidden by project constraints. |
| `/portfolio/{accId}/positions/0` | Yes, demo JS calls it | No for this project lane | Position/portfolio/account taint: yes | `accId` | Account id | Positions | No | Account access required | Forbidden by project constraints. |
| `/iserver/account/orders?force=false` and WebSocket `sor` / `uor` | Yes, documented in `RealtimeSubscription.md` | No for this project lane | Order taint: yes | `force=false` for REST; `sor+{}` for WebSocket | Authenticated account session | Live order data | No | Account/order access required | Forbidden by project constraints. |
| WebSocket `spl` / `upl` | Yes, documented in `RealtimeSubscription.md` | No for this project lane | PnL/position taint: yes | `spl+{}` / `upl{}` | Authenticated account session | PnL updates | No | Account/PnL access required | Forbidden by project constraints. |

## Local Swagger Finding

The bundled jar contains local Swagger files, but the IServer Swagger is not the full public Client Portal API spec. It only documents `/iserver/accounts`, which is account-tainted and not usable for this lane.

`GettingStarted.md` points users to an online Swagger URL and an external Client Portal guide. Those online sources were intentionally not fetched for this memo.

## Repo-Local Saved Evidence

The latest raw-shape inventory found:

- Raw snapshot inspected: `reports/manual_snapshots/ibkr_forecastex/20260526T104327Z`
- Raw files read: 29
- Endpoint shapes:
  - `/iserver/secdef/search`: 10 list responses
  - `/iserver/secdef/info`: 19 dict responses
- ForecastX identifiers found in all 29 raw files.
- Final tradable contract fields present: false.
- C/P right files: 0.
- Binary Yes/No files: 0.
- Final tradable contract field files: 0.
- Normalized diagnostic rows: 2.
- Final tradable rows: 0.

Current evidence supports `UNDERLIER_FOUND`, not `TRADABLE_CONTRACT_FOUND`.

## Blockers

- `local_docs_do_not_include_full_client_portal_openapi_spec`
- `option_lookup_doc_missing_locally`
- `final_tradable_forecastex_contract_endpoint_not_proven_locally`
- `current_saved_raw_shapes_underlier_only`
- `missing_call_put_right`
- `missing_expiry_or_month`
- `missing_strike_or_event_threshold`
- `underlier_only_no_tradable_contract_fields`
- `account_order_portfolio_paths_tainted`

## Recommendation

Do not run new live follow-up endpoints from this memo alone.

The safest next steps are:

1. Keep the current ForecastEx fetcher fail-closed with the existing read-only endpoint allow-list.
2. If the operator can provide official endpoint documentation from the local gateway or IBKR support that explicitly covers ForecastEx/Event Contract options-style contract enumeration, review it in a separate prompt before changing the allow-list.
3. If the operator can manually provide known final ForecastEx tradable conids, use the existing seed-conid path for saved read-only diagnostics.
4. Continue treating `FF` / ForecastEx rows as underlier discovery until final C/P or Yes/No contract fields are present in saved raw evidence.

## Safety Confirmation

This memo is review-only. It does not add endpoints, change fetcher behavior, call live APIs, touch account/order/portfolio/position/balance paths, create candidate pairs, or emit paper candidates.

## Addendum: Operator-Proven ForecastEx Options-Style Path

After the original local-docs-only memo, the operator manually verified a read-only Client Portal Gateway sequence for `FF` / US Fed Funds Target Rate:

- `/iserver/secdef/search?symbol=FF` returned ForecastEx underlier conid `658663572` with `sections` including `secType=EC`.
- `/iserver/secdef/strikes?conid=658663572&exchange=FORECASTX&sectype=OPT&month=JUN26` returned call and put strike arrays.
- `/iserver/secdef/info?conid=658663572&exchange=FORECASTX&sectype=OPT&month=JUN26&strike=4.375` returned C/P option-style ForecastEx rows.

The final rows contained diagnostic contract identity fields such as `conid`, `symbol=FF`, `secType=OPT`, `exchange/listingExchange/validExchanges=FORECASTX`, `right=C/P`, `strike`, `maturityDate`, `multiplier`, `currency`, `tradingClass`, and `desc2` labels ending in YES/NO at ForecastEx.

The fetcher now supports this proven path only when an explicit bounded `--forecastx-months` value is supplied. It does not guess month ranges. The path remains read-only secdef discovery and diagnostic-only:

- C maps to YES.
- P maps to NO.
- No order, account, balance, position, portfolio, credential, wallet, or signing endpoints are used.
- The resulting rows are not same-payoff evidence for any other venue.
- Any later economic use still requires quote/depth/freshness, fee, settlement/rules, and relationship review.

## Addendum: 2026-05-26 Diagnostic Readiness Boundary

Current read-only status:

- ForecastEx final C/P discovery is working through the bounded `search -> strikes -> info` path when explicit `--forecastx-months` is provided.
- Market-data snapshot diagnostics map explicit IBKR fields only: `84` bid, `86` ask, `88` bid size, `85` ask size, `_updated` quote timestamp, `6509` raw market-data status, and `31` raw last/close-ish value.
- Quote diagnostics remain partial. Missing top-of-book fields are blocked by `ibkr_forecastex_incomplete_top_of_book`; the prior `ibkr_forecastex_delayed_or_permission_limited_marketdata` label is preserved only as a legacy alias.
- Snapshot requests are bounded to at most 100 conids and 50 fields per request. A first conid-only response can be retried once, then remains blocked if still incomplete.
- Execution-ready rows remain `0`.

Source and venue warning:

IBKR unified UI can show Kalshi/CME/ForecastEx; `exchange_venue`, not tab/source platform alone, determines independence. IBKR-accessed Kalshi must not be treated as an independent venue from direct Kalshi.

Still required before exact-review or paper-review eligibility:

- Manual ForecastEx FF rules and settlement memo review.
- Fee and order-preview observation, diagnostic-only.
- Market-data permission and real-time/delayed status review.
- Settlement source and comparator/threshold semantics review.

The `forecastex_ibkr` source registry entry remains `PLANNED_NOT_IMPLEMENTED`. IBKR / ForecastEx rows remain diagnostic-only and cannot create candidate pairs or paper candidates.
