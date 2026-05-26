"""
Worker settings — all values configurable via environment variables.

Mandatory:
  DB_DSN  e.g. postgresql://user:pass@postgres:5432/mydb

Optional (all have sensible defaults):
  REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
  STREAM_NAME, CONSUMER_GROUP, CONSUMER_NAME
  BATCH_SIZE, BLOCK_MS, CLAIM_IDLE_MS
  DB_SCHEMA, DB_TABLE, DB_AUTO_MIGRATE
  LOG_LEVEL
"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class WorkerSettings(BaseSettings):
    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_stream_db: int = 1      # db=1 — stream only; db=0 is cache (dedup/rate-limit) in webhook-api
    redis_password: str = ""

    # ── Stream ────────────────────────────────────────────────────────────────
    stream_name: str = "webhook:batches"
    consumer_group: str = "pg-writers"
    consumer_name: str = "worker-1"
    # Number of messages to fetch per XREADGROUP call
    batch_size: int = 10
    # How long to block waiting for new messages (ms); 0 = non-blocking
    block_ms: int = 5000
    # Messages idle longer than this are reclaimed from crashed consumers (ms)
    claim_idle_ms: int = 60_000

    # ── Postgres ──────────────────────────────────────────────────────────────
    db_dsn: str  # required — no default
    db_schema: str = "public"
    db_table: str = "webhook_transactions"
    db_auto_migrate: bool = True

    # ── Misc ──────────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = WorkerSettings()
