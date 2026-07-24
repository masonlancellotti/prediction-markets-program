# Archived

This repository is an archived research monorepo and is no longer maintained.
It bundled several exploratory prediction-market subprojects built during a
short research sprint.

The strongest component — the market-graph consistency scanner — has been
extracted into its own standalone, maintained repository:

**market-graph-consistency** — detects logical inconsistencies and
no-arbitrage violations across related prediction-market contracts. It models
SUBSET / IMPLIES / exclusivity relationships as a graph, runs bounded
no-arbitrage checks, ships 655 hermetic tests, and requires no API keys.

Please use that repository for current, supported code. The remaining
subprojects here (kalshi-weather-edge, relative-value-scanner) are retained
only as a historical snapshot and receive no further updates.
