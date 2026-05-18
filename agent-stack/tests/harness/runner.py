"""Enterprise Test Harness Runner — RL-integrated test execution.

This is the command-line test runner that:
1. Discovers all tests in the suite
2. Uses RL optimizer to prioritize test execution order
3. Records results back to the RL optimizer for learning
4. Generates comprehensive test reports
5. Supports CI budget mode (run only what fits in time budget)

Usage:
    # Run full suite with RL prioritization
    python -m tests.harness.runner

    # Run with CI time budget (60 seconds)
    python -m tests.harness.runner --budget 60

    # Run only high-priority tests
    python -m tests.harness.runner --min-priority 0.7

    # Generate RL learning report
    python -m tests.harness.runner --report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class TestRunResult:
    """Result of a single test run."""
    name: str
    passed: bool
    duration_ms: float
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SuiteReport:
    """Full test suite execution report."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_duration_ms: float = 0
    rl_prioritized: bool = False
    results: list[TestRunResult] = field(default_factory=list)
    flaky_tests: list[dict] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.passed / self.total * 100, 1)

    @property
    def avg_duration_ms(self) -> float:
        if not self.results:
            return 0.0
        return round(sum(r.duration_ms for r in self.results) / len(self.results), 1)


class TestHarnessRunner:
    """Enterprise test harness with RL-driven prioritization.

    Integrates with RLTestOptimizer to:
    1. Prioritize tests that catch regressions
    2. Deprioritize always-passing, slow, or flaky tests
    3. Select tests within CI time budgets
    4. Learn from every run to improve future prioritization
    """

    def __init__(self, rl_db_path: str = "data/test_rl.db"):
        self.rl_db_path = str(PROJECT_ROOT / rl_db_path)

    async def run(
        self,
        budget_seconds: float | None = None,
        min_priority: float = 0.0,
        record_to_rl: bool = True,
    ) -> SuiteReport:
        """Execute the test suite with RL prioritization.

        Args:
            budget_seconds: If set, only run tests that fit within this budget.
            min_priority: Only run tests with priority >= this value.
            record_to_rl: Whether to record results to the RL optimizer.
        """
        from tests.rl.optimizer import RLTestOptimizer

        report = SuiteReport()

        # Discover tests via pytest collection
        test_names = self._discover_tests()
        report.total = len(test_names)

        if not test_names:
            print("⚠ No tests discovered")
            return report

        # Initialize RL optimizer
        optimizer = RLTestOptimizer(db_path=self.rl_db_path)
        await optimizer.initialize()

        # Prioritize tests using RL
        prioritized = await optimizer.prioritize(test_names)
        report.rl_prioritized = True

        # Filter by minimum priority
        if min_priority > 0:
            filtered = []
            for name in prioritized:
                cursor = await optimizer._db.execute(
                    "SELECT priority_score FROM test_scores WHERE test_name = ?",
                    (name,)
                )
                row = await cursor.fetchone()
                score = row["priority_score"] if row else 0.5
                if score >= min_priority:
                    filtered.append(name)
            prioritized = filtered

        # Apply CI budget if specified
        if budget_seconds is not None:
            selected = await optimizer.select_for_ci(prioritized, budget_seconds)
            report.skipped = len(prioritized) - len(selected)
            prioritized = selected

        print(f"\n🚀 Running {len(prioritized)} tests (RL-prioritized)")
        if report.skipped:
            print(f"   Skipped {report.skipped} tests (below threshold or budget)")
        print()

        # Run tests via pytest
        results = await self._run_pytest(prioritized)
        report.results = results

        # Tally results
        for r in results:
            if r.passed:
                report.passed += 1
            else:
                report.failed += 1
            report.total_duration_ms += r.duration_ms

        # Record to RL optimizer
        if record_to_rl:
            for r in results:
                await optimizer.record(
                    test_name=r.name,
                    passed=r.passed,
                    duration_ms=r.duration_ms,
                )
            print(f"\n📊 Recorded {len(results)} results to RL optimizer")

        # Get flaky test report
        report.flaky_tests = await optimizer.flaky_report()

        # Get coverage gaps
        gap_report = await optimizer.coverage_gap_report(test_names)
        report.coverage_gaps = gap_report.get("unscored", [])

        await optimizer.shutdown()
        return report

    def _discover_tests(self) -> list[str]:
        """Discover test names using pytest collection."""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", "tests/"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
        )
        test_names = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if "::test_" in line:
                test_names.append(line)
        return test_names

    async def _run_pytest(self, test_names: list[str]) -> list[TestRunResult]:
        """Run specific tests via pytest and parse results."""
        if not test_names:
            return []

        import subprocess
        # Run pytest with JSON report for structured results
        args = [
            "python", "-m", "pytest",
            *test_names,
            "--json-report",
            "--json-report-file=-",  # stdout
            "-q",
        ]

        proc = subprocess.run(
            args, capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
        )

        # Parse JSON report from stdout
        results = []
        try:
            # pytest-json-report outputs JSON to stdout
            report_data = json.loads(proc.stdout)
            for test in report_data.get("tests", []):
                duration_ms = test.get("duration", 0) * 1000
                outcome = test.get("outcome", "failed")
                error = ""
                if outcome == "failed" and "call" in test:
                    call_info = test["call"]
                    if "crash" in call_info:
                        error = call_info["crash"].get("message", "")[:200]
                results.append(TestRunResult(
                    name=test.get("nodeid", "unknown"),
                    passed=outcome == "passed",
                    duration_ms=round(duration_ms, 1),
                    error=error,
                ))
        except json.JSONDecodeError:
            # Fallback: parse from stdout lines
            for line in proc.stdout.splitlines():
                if "PASSED" in line or "FAILED" in line:
                    parts = line.strip().split()
                    if parts:
                        name = parts[0]
                        passed = "PASSED" in line
                        results.append(TestRunResult(
                            name=name, passed=passed, duration_ms=0
                        ))

        return results

    def print_report(self, report: SuiteReport) -> None:
        """Print a formatted test suite report."""
        print("\n" + "=" * 70)
        print("  COMPOSABLE AGENT STACK — TEST SUITE REPORT")
        print("=" * 70)
        print(f"  Total:    {report.total}")
        print(f"  Passed:   {report.passed} ✅")
        print(f"  Failed:   {report.failed} {'❌' if report.failed else ''}")
        print(f"  Skipped:  {report.skipped}")
        print(f"  Pass Rate: {report.pass_rate}%")
        print(f"  Duration:  {report.total_duration_ms:.0f}ms (avg {report.avg_duration_ms}ms/test)")
        print(f"  RL Prioritized: {'Yes' if report.rl_prioritized else 'No'}")

        if report.failed > 0:
            print("\n  FAILED TESTS:")
            for r in report.results:
                if not r.passed:
                    print(f"    ❌ {r.name}")
                    if r.error:
                        print(f"       {r.error[:100]}")

        if report.flaky_tests:
            print(f"\n  FLAKY TESTS ({len(report.flaky_tests)}):")
            for ft in report.flaky_tests[:5]:
                print(f"    ⚠ {ft['test_name']} (flakiness={ft['flakiness_score']})")

        if report.coverage_gaps:
            print(f"\n  COVERAGE GAPS ({len(report.coverage_gaps)} tests never recorded):")
            for gap in report.coverage_gaps[:5]:
                print(f"    🔍 {gap}")
            if len(report.coverage_gaps) > 5:
                print(f"    ... and {len(report.coverage_gaps) - 5} more")

        print("=" * 70)


async def main():
    parser = argparse.ArgumentParser(description="RL-integrated test harness runner")
    parser.add_argument("--budget", type=float, default=None,
                        help="CI time budget in seconds")
    parser.add_argument("--min-priority", type=float, default=0.0,
                        help="Minimum RL priority score to run (0.0-1.0)")
    parser.add_argument("--no-rl", action="store_true",
                        help="Don't record results to RL optimizer")
    parser.add_argument("--report-only", action="store_true",
                        help="Only show RL summary report, don't run tests")
    args = parser.parse_args()

    runner = TestHarnessRunner()

    if args.report_only:
        from tests.rl.optimizer import RLTestOptimizer
        opt = RLTestOptimizer(db_path=runner.rl_db_path)
        await opt.initialize()
        summary = await opt.summary()
        print(json.dumps(summary, indent=2))
        await opt.shutdown()
        return

    report = await runner.run(
        budget_seconds=args.budget,
        min_priority=args.min_priority,
        record_to_rl=not args.no_rl,
    )
    runner.print_report(report)

    # Exit with failure if any tests failed
    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
