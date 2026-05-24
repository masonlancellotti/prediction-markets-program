# Recovery Context Packet

Generated only after failure, compaction, timeout, or interruption.

This file should contain compact stable context, current lane state, latest summaries/reviews, command result tails, failure log tail, and small file-change summaries. It should not become a permanent giant context dump.
