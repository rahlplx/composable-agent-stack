"""Z.ai Execution Memory — SQLite Compression Manager.

This is NOT a product feature. It is Z.ai's own context management tool
that auto-triggers /compact to prevent context overflow during long
execution runs (like building the composable agent stack).

Key design goals:
- Multiple isolated sessions (different work streams)
- /compact auto-triggers when context exceeds threshold
- Critical knowledge is NEVER deleted, only compressed
- Compact history is auditable
- TTL cleanup for stale sessions
"""
