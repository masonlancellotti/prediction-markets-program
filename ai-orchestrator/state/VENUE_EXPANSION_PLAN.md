# Venue Expansion Plan

New venues are roadmap inputs, not permission to add auth, scraping, account reads, or execution.

| Venue | source_type | status | executable? | auth needed? | user action needed? | next safe task |
| --- | --- | --- | --- | --- | --- | --- |
| Kalshi | exchange | read-only active | yes, only where already approved/read-only | do not add | no | Maintain conservative read-only diagnostics. |
| Polymarket | exchange | read-only active | yes, only where already approved/read-only | do not add | no | Maintain conservative fee/depth/freshness gates. |
| SX Bet | venue | research-only | no | yes later | yes before integration | Keep research-only note and ask Mason before any connector work. |
| ForecastEx/IBKR | venue | fixture-backed | no live transport | yes later | yes before integration | Fixture-backed schema notes only. |
| ProphetX | venue | fixture-backed | no live transport | yes later | yes before integration | Fixture-backed schema notes only. |
| The Odds API | reference | reference-only | no | yes later | yes before integration | Reference-only comparison notes; never executable. |
| Sportsbooks | reference | reference-only | no | varies | yes before integration | Reference-only; do not treat as executable legs. |
| Other venues | unknown | pending user connection | no | yes | yes | Write USER_ACTION_REQUIRED entry before work. |
