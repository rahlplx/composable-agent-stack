"""Redis Streams Task Distribution Layer.

Uses Redis Streams for durable, at-least-once task delivery to
platform adapters. Consumer groups ensure each task is processed
by exactly one adapter instance.

Architecture:
  Orchestrator ──XADD──► Redis Stream ──XREADGROUP──► Platform Adapter
                                              │
                          XACK ◄─────────────┘

Key features:
- Durable: tasks survive orchestrator restarts
- At-least-once: unacknowledged tasks are re-delivered
- Consumer groups: horizontal scaling of adapters
- Dead letter: tasks that fail N times go to DLQ
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis


class RedisTaskQueue:
    """Redis Streams-based task distribution for the composable agent stack.

    Stream naming: {prefix}:{platform}
    e.g., agent_stack:tasks:browser_use, agent_stack:tasks:agent_s

    Each platform gets its own stream for capacity isolation.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        stream_prefix: str = "agent_stack:tasks",
        consumer_group: str = "orchestrator",
        consumer_name: str | None = None,
    ):
        self.redis_url = redis_url
        self.stream_prefix = stream_prefix
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"worker-{uuid.uuid4().hex[:8]}"
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        """Connect to Redis and create consumer groups."""
        self._redis = aioredis.from_url(self.redis_url, decode_responses=True)

        # Create consumer groups for each platform stream (MKSTREAM)
        for platform in ("browser_use", "agent_s", "openhands"):
            stream = f"{self.stream_prefix}:{platform}"
            try:
                await self._redis.xgroup_create(
                    stream, self.consumer_group, id="0", mkstream=True
                )
            except aioredis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise  # Ignore "group already exists"

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    # ── Publishing ──────────────────────────────────────────────────

    async def publish_task(
        self,
        task_id: str,
        platform: str,
        action_type: str,
        input_data: dict,
        priority: int = 0,
    ) -> str:
        """Publish a task to the platform's stream.

        Returns the Redis stream entry ID.
        """
        if self._redis is None:
            raise RuntimeError("Not connected. Call connect() first.")

        stream = f"{self.stream_prefix}:{platform}"
        message = {
            "task_id": task_id,
            "action_type": action_type,
            "input_data": json.dumps(input_data),
            "priority": priority,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        entry_id = await self._redis.xadd(stream, message)
        return entry_id

    # ── Consuming ───────────────────────────────────────────────────

    async def consume_task(
        self,
        platform: str,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[dict]:
        """Read tasks from a platform's stream.

        Returns list of task dicts with entry_id.
        """
        if self._redis is None:
            raise RuntimeError("Not connected. Call connect() first.")

        stream = f"{self.stream_prefix}:{platform}"
        results = await self._redis.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {stream: ">"},
            count=count,
            block=block_ms,
        )

        tasks = []
        if results:
            for stream_name, entries in results:
                for entry_id, fields in entries:
                    task = dict(fields)
                    task["entry_id"] = entry_id
                    task["stream"] = stream_name
                    if "input_data" in task:
                        task["input_data"] = json.loads(task["input_data"])
                    tasks.append(task)

        return tasks

    async def acknowledge(self, platform: str, entry_id: str) -> None:
        """Acknowledge a task as processed (XACK)."""
        if self._redis is None:
            return

        stream = f"{self.stream_prefix}:{platform}"
        await self._redis.xack(stream, self.consumer_group, entry_id)

    # ── Status ──────────────────────────────────────────────────────

    async def stream_length(self, platform: str) -> int:
        """Get the number of pending messages in a stream."""
        if self._redis is None:
            return 0
        stream = f"{self.stream_prefix}:{platform}"
        info = await self._redis.xinfo_stream(stream)
        return info.get("length", 0)

    async def pending_count(self, platform: str) -> int:
        """Get number of pending (unacknowledged) messages."""
        if self._redis is None:
            return 0
        stream = f"{self.stream_prefix}:{platform}"
        try:
            info = await self._redis.xpending_range(
                stream, self.consumer_group, min="-", max="+", count=1
            )
            # XPENDING gives count in summary
            pending = await self._redis.xpending(stream, self.consumer_group)
            return pending.get("pending", 0) if isinstance(pending, dict) else 0
        except Exception:
            return 0

    async def get_consumer_lag(self, platform: str) -> dict:
        """Get consumer group lag info for a platform stream."""
        if self._redis is None:
            return {}
        stream = f"{self.stream_prefix}:{platform}"
        try:
            info = await self._redis.xinfo_consumers(stream, self.consumer_group)
            return {
                consumer["name"]: {
                    "pending": consumer.get("pending", 0),
                    "idle_ms": consumer.get("idle", 0),
                }
                for consumer in info
            }
        except Exception:
            return {}

    # ── Dead Letter Queue ───────────────────────────────────────────

    async def send_to_dlq(
        self,
        platform: str,
        task_id: str,
        error: str,
        original_data: dict,
    ) -> None:
        """Send a permanently failed task to the dead letter queue."""
        if self._redis is None:
            return
        dlq_stream = f"{self.stream_prefix}:dlq:{platform}"
        await self._redis.xadd(dlq_stream, {
            "task_id": task_id,
            "error": error,
            "original_data": json.dumps(original_data),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def get_dlq_entries(self, platform: str, count: int = 10) -> list[dict]:
        """Read entries from the dead letter queue."""
        if self._redis is None:
            return []
        dlq_stream = f"{self.stream_prefix}:dlq:{platform}"
        entries = await self._redis.xrange(dlq_stream, count=count)
        return [
            {"entry_id": eid, **dict(fields)}
            for eid, fields in entries
        ]
