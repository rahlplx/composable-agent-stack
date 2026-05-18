"""Application configuration via pydantic-settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration. All values can be overridden via env vars."""

    # ── General ──────────────────────────────────────────────────────
    app_name: str = "composable-agent-stack"
    debug: bool = False
    log_level: str = "INFO"

    # ── SQLite Compression Manager ───────────────────────────────────
    sqlite_db_path: str = str(Path(__file__).resolve().parent.parent / "data" / "compression.db")
    compact_threshold_bytes: int = 50_000       # trigger /compact above this
    compact_max_snapshot_bytes: int = 200_000   # hard ceiling per snapshot
    session_ttl_hours: int = 72                 # inactive session cleanup
    auto_compact_interval_seconds: int = 60     # systematic auto-trigger

    # ── PostgreSQL (state persistence) ───────────────────────────────
    postgres_url: str = "postgresql+asyncpg://agent:agent@localhost:5432/agent_stack"

    # ── Redis (task queue) ───────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_prefix: str = "agent_stack:tasks"

    # ── LiteLLM ──────────────────────────────────────────────────────
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""

    # ── Platform Adapters ────────────────────────────────────────────
    agent_s_endpoint: str = "http://localhost:8001"
    browser_use_endpoint: str = "http://localhost:8002"
    openhands_endpoint: str = "http://localhost:8003"

    # ── Task Execution ───────────────────────────────────────────────
    max_retries: int = 3
    retry_backoff_base: float = 2.0             # exponential backoff
    task_timeout_seconds: int = 600             # 10 minutes

    model_config = {"env_prefix": "AGENT_STACK_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
