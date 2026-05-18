"""RL Test Optimizer — Reinforcement Learning for test suite efficiency.

This module implements a lightweight RL-based test optimizer that:
1. Tracks test execution history (pass/fail, duration, flakiness)
2. Learns which tests are most likely to catch regressions
3. Prioritizes tests that are: fast + high-regression-catch-rate + stable
4. Detects flaky tests (intermittent failures)
5. Adapts test selection based on code changes

The "reward signal" is:
- +1.0 for catching a real regression (test fails when code changed)
- +0.5 for being fast (<100ms) and stable
- -0.3 for being flaky (pass/fail inconsistently)
- -0.5 for being slow (>1s) without catching regressions
- -1.0 for being always-passing (never catches anything)

Usage:
    optimizer = RLTestOptimizer(db_path="data/test_rl.db")
    await optimizer.initialize()

    # Record test results
    await optimizer.record("test_compact_preserves_critical", passed=True, duration_ms=50)

    # Get prioritized test order
    priority_order = await optimizer.prioritize(all_test_names)

    # Get flaky test report
    report = await optimizer.flaky_report()
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS test_history (
    id TEXT PRIMARY KEY,
    test_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    timestamp TEXT NOT NULL,
    commit_hash TEXT NOT NULL DEFAULT '',
    module TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_scores (
    test_name TEXT PRIMARY KEY,
    priority_score REAL NOT NULL DEFAULT 0.5,
    regression_catch_count INTEGER NOT NULL DEFAULT 0,
    total_runs INTEGER NOT NULL DEFAULT 0,
    total_passes INTEGER NOT NULL DEFAULT 0,
    total_fails INTEGER NOT NULL DEFAULT 0,
    avg_duration_ms REAL NOT NULL DEFAULT 0,
    flakiness_score REAL NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_test ON test_history(test_name);
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON test_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_scores_priority ON test_scores(priority_score DESC);
"""


@dataclass
class ScoreEntry:
    """RL-learned priority score for a test."""
    test_name: str
    priority_score: float = 0.5     # 0.0 (skip) to 1.0 (must run)
    regression_catch_count: int = 0
    total_runs: int = 0
    total_passes: int = 0
    total_fails: int = 0
    avg_duration_ms: float = 0
    flakiness_score: float = 0      # 0.0 (stable) to 1.0 (very flaky)
    last_updated: str = ""

    @property
    def pass_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_passes / self.total_runs

    @property
    def is_flaky(self) -> bool:
        return self.flakiness_score > 0.3 and self.total_runs >= 5

    @property
    def is_always_passing(self) -> bool:
        return self.total_runs >= 10 and self.total_fails == 0

    @property
    def efficiency_score(self) -> float:
        """Score combining regression detection, speed, and stability."""
        if self.total_runs == 0:
            return 0.5
        regression_weight = min(self.regression_catch_count / max(self.total_runs, 1), 1.0)
        speed_weight = 1.0 if self.avg_duration_ms < 100 else max(0, 1 - self.avg_duration_ms / 5000)
        stability_weight = 1.0 - self.flakiness_score
        return (regression_weight * 0.5 + speed_weight * 0.2 + stability_weight * 0.3)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ScoreEntry:
        return cls(**dict(row))


class RLTestOptimizer:
    """Reinforcement Learning test suite optimizer.

    Tracks test execution history and learns which tests to prioritize
    for maximum regression detection in minimum time.
    """

    def __init__(self, db_path: str = "data/test_rl.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def shutdown(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Recording ────────────────────────────────────────────────────

    async def record(
        self,
        test_name: str,
        passed: bool,
        duration_ms: float,
        commit_hash: str = "",
        module: str = "",
    ) -> None:
        """Record a test execution result."""
        if self._db is None:
            return

        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO test_history (id, test_name, passed, duration_ms, timestamp, commit_hash, module)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, test_name, 1 if passed else 0, duration_ms, now, commit_hash, module)
        )

        # Update aggregate score
        await self._update_score(test_name, passed, duration_ms)
        await self._db.commit()

    async def record_batch(self, results: list[dict]) -> None:
        """Record multiple test results at once."""
        for r in results:
            await self.record(**r)

    async def _update_score(self, test_name: str, passed: bool, duration_ms: float) -> None:
        """Update the RL score for a test based on new result."""
        # Get current score
        cursor = await self._db.execute(
            "SELECT * FROM test_scores WHERE test_name = ?", (test_name,)
        )
        row = await cursor.fetchone()

        if row is None:
            score = ScoreEntry(test_name=test_name)
        else:
            score = ScoreEntry.from_row(row)

        # Update counts
        score.total_runs += 1
        if passed:
            score.total_passes += 1
        else:
            score.total_fails += 1
            # A failure after previous passes = potential regression catch
            # Check if previous run passed
            cursor = await self._db.execute(
                "SELECT passed FROM test_history WHERE test_name = ? ORDER BY timestamp DESC LIMIT 1 OFFSET 1",
                (test_name,)
            )
            prev = await cursor.fetchone()
            if prev and prev["passed"] == 1:
                score.regression_catch_count += 1

        # Update average duration (exponential moving average)
        alpha = 0.3  # weight for new observation
        score.avg_duration_ms = (
            alpha * duration_ms + (1 - alpha) * score.avg_duration_ms
            if score.total_runs > 1 else duration_ms
        )

        # Update flakiness score
        # Flakiness = how often the result flips between pass and fail
        if score.total_runs >= 3:
            cursor = await self._db.execute(
                "SELECT passed FROM test_history WHERE test_name = ? ORDER BY timestamp DESC LIMIT 10",
                (test_name,)
            )
            recent = await cursor.fetchall()
            flips = 0
            for i in range(1, len(recent)):
                if recent[i]["passed"] != recent[i - 1]["passed"]:
                    flips += 1
            score.flakiness_score = flips / max(len(recent) - 1, 1)

        # Calculate priority score using RL reward signal
        score.priority_score = self._calculate_priority(score)
        score.last_updated = datetime.now(timezone.utc).isoformat()

        # Upsert
        await self._db.execute(
            """INSERT OR REPLACE INTO test_scores
               (test_name, priority_score, regression_catch_count, total_runs,
                total_passes, total_fails, avg_duration_ms, flakiness_score, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (score.test_name, score.priority_score, score.regression_catch_count,
             score.total_runs, score.total_passes, score.total_fails,
             score.avg_duration_ms, score.flakiness_score, score.last_updated)
        )

    def _calculate_priority(self, score: ScoreEntry) -> float:
        """RL reward calculation: priority score.

        High priority = likely to catch regressions + fast + stable
        Low priority = always passing (no regression signal) or very flaky

        Reward signal:
        - regression_catch_count: +0.4 per catch (capped at 1.0)
        - speed: +0.2 if avg < 100ms, -0.1 if avg > 2000ms
        - stability: +0.2 if flakiness < 0.1, -0.3 if flakiness > 0.5
        - always_passing penalty: -0.2 if 10+ runs, 0 failures
        """
        reward = 0.5  # base

        # Regression detection bonus
        regression_bonus = min(score.regression_catch_count * 0.15, 0.4)
        reward += regression_bonus

        # Speed bonus/penalty
        if score.avg_duration_ms < 100:
            reward += 0.2
        elif score.avg_duration_ms > 2000:
            reward -= 0.1

        # Stability bonus/penalty
        if score.flakiness_score < 0.1:
            reward += 0.2
        elif score.flakiness_score > 0.5:
            reward -= 0.3

        # Always-passing penalty (no regression detection value)
        if score.is_always_passing:
            reward -= 0.2

        return max(0.0, min(1.0, reward))

    # ── Prioritization ───────────────────────────────────────────────

    async def prioritize(self, test_names: list[str]) -> list[str]:
        """Return test names sorted by RL priority (highest first).

        Tests with higher priority_score run first to catch regressions faster.
        """
        if self._db is None:
            return test_names

        scores = {}
        for name in test_names:
            cursor = await self._db.execute(
                "SELECT priority_score FROM test_scores WHERE test_name = ?",
                (name,)
            )
            row = await cursor.fetchone()
            scores[name] = row["priority_score"] if row else 0.5

        return sorted(test_names, key=lambda n: scores[n], reverse=True)

    async def select_for_ci(self, test_names: list[str], budget_seconds: float = 60.0) -> list[str]:
        """Select tests to run within a time budget.

        Greedy selection: pick highest priority tests that fit within budget
        based on average duration.
        """
        if self._db is None:
            return test_names

        selected = []
        remaining_budget = budget_seconds * 1000  # convert to ms

        for name in await self.prioritize(test_names):
            cursor = await self._db.execute(
                "SELECT avg_duration_ms, priority_score FROM test_scores WHERE test_name = ?",
                (name,)
            )
            row = await cursor.fetchone()
            duration = row["avg_duration_ms"] if row else 50  # default 50ms
            priority = row["priority_score"] if row else 0.5

            # Always include high-priority tests regardless of budget
            if priority > 0.8 or remaining_budget >= duration:
                selected.append(name)
                remaining_budget -= duration

            if remaining_budget <= 0 and not any(
                # Don't stop if there are still critical tests
                True for n in test_names if n not in selected
                and (await self._get_priority(n)) > 0.8
            ):
                break

        return selected

    async def _get_priority(self, test_name: str) -> float:
        cursor = await self._db.execute(
            "SELECT priority_score FROM test_scores WHERE test_name = ?",
            (test_name,)
        )
        row = await cursor.fetchone()
        return row["priority_score"] if row else 0.5

    # ── Reporting ────────────────────────────────────────────────────

    async def get_scores(self) -> list[ScoreEntry]:
        """Get all test scores."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT * FROM test_scores ORDER BY priority_score DESC"
        )
        rows = await cursor.fetchall()
        return [ScoreEntry.from_row(r) for r in rows]

    async def flaky_report(self) -> list[dict]:
        """Get tests flagged as flaky."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT * FROM test_scores WHERE flakiness_score > 0.3 AND total_runs >= 5 ORDER BY flakiness_score DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "test_name": r["test_name"],
                "flakiness_score": round(r["flakiness_score"], 3),
                "total_runs": r["total_runs"],
                "pass_rate": round(r["total_passes"] / max(r["total_runs"], 1), 3),
                "avg_duration_ms": round(r["avg_duration_ms"], 1),
            }
            for r in rows
        ]

    async def coverage_gap_report(self, all_test_names: list[str]) -> dict:
        """Identify tests with no score (never recorded) = potential coverage gap."""
        if self._db is None:
            return {"unscored": all_test_names, "count": len(all_test_names)}

        scored = set()
        cursor = await self._db.execute("SELECT test_name FROM test_scores")
        rows = await cursor.fetchall()
        for r in rows:
            scored.add(r["test_name"])

        unscored = [n for n in all_test_names if n not in scored]
        return {
            "unscored": unscored,
            "count": len(unscored),
            "total": len(all_test_names),
            "coverage_pct": round((1 - len(unscored) / max(len(all_test_names), 1)) * 100, 1),
        }

    async def summary(self) -> dict:
        """Full test suite health summary."""
        if self._db is None:
            return {}
        scores = await self.get_scores()
        return {
            "total_tracked": len(scores),
            "avg_priority": round(sum(s.priority_score for s in scores) / max(len(scores), 1), 3),
            "flaky_count": sum(1 for s in scores if s.is_flaky),
            "always_passing_count": sum(1 for s in scores if s.is_always_passing),
            "avg_duration_ms": round(sum(s.avg_duration_ms for s in scores) / max(len(scores), 1), 1),
            "total_regression_catches": sum(s.regression_catch_count for s in scores),
            "top_priority": [
                {"name": s.test_name, "score": round(s.priority_score, 3)}
                for s in sorted(scores, key=lambda x: x.priority_score, reverse=True)[:5]
            ],
        }
