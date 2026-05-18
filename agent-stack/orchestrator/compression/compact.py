"""Compact tool — the /compress logic that merges and summarizes context."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompactResult:
    """Result of a compact operation."""

    session_id: str
    entries_before: int = 0
    entries_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    compression_ratio: float = 0.0
    categories_merged: list[str] = field(default_factory=list)

    @property
    def savings_pct(self) -> float:
        if self.bytes_before == 0:
            return 0.0
        return (1 - self.bytes_after / self.bytes_before) * 100


def compact_entries(entries: list[dict]) -> list[dict]:
    """Merge and compress context entries.

    Strategy:
    1. Deduplicate by (session_id, category, key) — keep latest
    2. Merge small entries in the same category into summary blocks
    3. Strip redundant fields

    Returns the compressed list of entries.
    """
    if not entries:
        return []

    # Step 1: Deduplicate — keep latest by updated_at / insertion order
    seen: dict[tuple[str, str], dict] = {}
    for entry in entries:
        key = (entry.get("category", ""), entry.get("key", ""))
        seen[key] = entry  # last write wins

    deduped = list(seen.values())

    # Step 2: Group by category — merge small entries into summaries
    by_category: dict[str, list[dict]] = {}
    for entry in deduped:
        cat = entry.get("category", "uncategorized")
        by_category.setdefault(cat, []).append(entry)

    result: list[dict] = []
    merge_threshold = 5  # merge if >5 entries in same category

    for cat, cat_entries in by_category.items():
        if len(cat_entries) <= merge_threshold:
            result.extend(cat_entries)
        else:
            # Merge into a summary entry + keep the most recent N
            merged_values = {}
            for e in cat_entries:
                k = e.get("key", "unknown")
                merged_values[k] = e.get("value")

            summary_entry = {
                "category": cat,
                "key": f"_merged_{cat}_summary",
                "value": merged_values,
                "is_summary": True,
            }
            # Keep the 3 most recent entries individually + summary
            result.append(summary_entry)
            for e in cat_entries[-3:]:
                result.append(e)

    return result
