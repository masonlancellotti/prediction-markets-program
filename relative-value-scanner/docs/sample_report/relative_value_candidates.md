# Relative Value Candidates

Read-only offline scan. Sportsbook odds are reference-only and cannot create POSSIBLE_ARB.

| Action | Left | Left Outcome | Right | Right Outcome | Confidence | Mismatch Risk | Liquidity Top Contracts | Gap | Notes |
|---|---|---|---|---|---:|---:|---:|---:|---|
| MANUAL_REVIEW | kalshi:KAL_NBA_CLE_TEAM_TOTAL_91_OVER | Cleveland over 91.5 points | polymarket:POLY_NBA_CLE_TEAM_TOTAL_91_OVER | Cleveland over 91.5 points | 1.00 | 0.00 | 75.00 | -0.024 | no_side_spread_assumed; settlement times align; positive gross gap needs manual review |
| WATCH | kalshi:KAL_KY_TURNOUT_120K | turnout above 120000 | polymarket:POLY_KY_TURNOUT_120K | turnout over 120000 | 0.55 | 0.60 | 15.00 | -0.030 | no_side_spread_assumed; settlement mismatch risk blocks POSSIBLE_ARB; high settlement mismatch risk caps action at WATCH |
| WATCH | kalshi:KAL_MLB_EXTRA_INNINGS_PIT_STL | game goes to extra innings | polymarket:POLY_MLB_EXTRA_INNINGS_PIT_STL | game reaches extra innings | 0.97 | 0.00 | 18.00 | -0.060 | no_side_spread_assumed; settlement times align; matched executable markets without positive gap |
| WATCH | kalshi:KAL_NBA_CLE_TEAM_TOTAL_91_OVER | Cleveland over 91.5 points | fixture_sportsbook:fixture_sportsbook:odds_nba_cle_team_total_91:Cleveland over 91.5 points | Cleveland over 91.5 points | 0.80 | 0.25 | 0.00 | 0.362 | quote_freshness_unverified; side_definition_unverified; settlement mismatch risk blocks POSSIBLE_ARB |
| WATCH | polymarket:POLY_NBA_CLE_TEAM_TOTAL_91_OVER | Cleveland over 91.5 points | fixture_sportsbook:fixture_sportsbook:odds_nba_cle_team_total_91:Cleveland over 91.5 points | Cleveland over 91.5 points | 0.80 | 0.25 | 0.00 | 0.254 | quote_freshness_unverified; side_definition_unverified; settlement mismatch risk blocks POSSIBLE_ARB |
| WATCH | polymarket:POLY_MLB_EXTRA_INNINGS_PIT_STL | game reaches extra innings | fixture_sportsbook:fixture_sportsbook:odds_mlb_extra_pit_stl:game goes to extra innings | game goes to extra innings | 0.80 | 0.25 | 0.00 | 0.077 | quote_freshness_unverified; side_definition_unverified; settlement mismatch risk blocks POSSIBLE_ARB |
| WATCH | kalshi:KAL_MLB_EXTRA_INNINGS_PIT_STL | game goes to extra innings | fixture_sportsbook:fixture_sportsbook:odds_mlb_extra_pit_stl:game goes to extra innings | game goes to extra innings | 0.80 | 0.25 | 0.00 | 0.007 | quote_freshness_unverified; side_definition_unverified; settlement mismatch risk blocks POSSIBLE_ARB |
