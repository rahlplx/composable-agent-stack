"""Auto-Compact Daemon — systematically triggers /compact.

Runs as a background task within the orchestrator, periodically
checking all active sessions and triggering compact when they
exceed the threshold. This is the systematic /compact that
eliminates context overflow issues.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from orchestrator.compression.manager import CompressionManager, SessionStatus

logger = logging.getLogger("agent-stack.autocompact")


class AutoCompactDaemon:
    """Background daemon that auto-triggers /compact on sessions.

    Runs on a configurable interval (default: 60s).
    For each active session, checks if it exceeds the threshold
    and compacts if needed. Also performs TTL cleanup.

    This is the "systematic /compact" that keeps Z.ai's execution
    memory from overflowing during long runs.
    """

    def __init__(
        self,
        compression_manager: CompressionManager,
        interval_seconds: int = 60,
        cleanup_interval_seconds: int = 600,  # TTL cleanup every 10 min
    ):
        self.mem = compression_manager
        self.interval_seconds = interval_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._compact_count = 0
        self._cleanup_count = 0

    async def start(self) -> None:
        """Start the auto-compact daemon."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"AutoCompactDaemon started (interval={self.interval_seconds}s, "
            f"cleanup={self.cleanup_interval_seconds}s)"
        )

    async def stop(self) -> None:
        """Stop the daemon."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"AutoCompactDaemon stopped (compacts={self._compact_count}, cleanups={self._cleanup_count})")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "compact_count": self._compact_count,
            "cleanup_count": self._cleanup_count,
            "interval_seconds": self.interval_seconds,
        }

    async def _run_loop(self) -> None:
        """Main loop: compact check + periodic cleanup."""
        cleanup_counter = 0

        while self._running:
            try:
                # Check all active sessions
                sessions = await self.mem.list_sessions(status=SessionStatus.ACTIVE)

                for session in sessions:
                    size = await self.mem.get_session_size(session.id)
                    if size > self.mem.compact_threshold_bytes:
                        result = await self.mem.compact(session.id, triggered_by="auto")
                        self._compact_count += 1
                        logger.info(
                            f"Auto-compact: session={session.name} "
                            f"({result.entries_before}→{result.entries_after} entries, "
                            f"{result.bytes_before}→{result.bytes_after} bytes, "
                            f"savings={result.savings_pct}%)"
                        )

                # Periodic TTL cleanup
                cleanup_counter += 1
                if cleanup_counter * self.interval_seconds >= self.cleanup_interval_seconds:
                    cleaned = await self.mem.cleanup_expired()
                    if cleaned > 0:
                        self._cleanup_count += cleaned
                        logger.info(f"TTL cleanup: removed {cleaned} expired sessions")
                    cleanup_counter = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Auto-compact error: {e}")

            await asyncio.sleep(self.interval_seconds)
