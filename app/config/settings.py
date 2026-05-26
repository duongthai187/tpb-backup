from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8443
    reload: bool = False

    # ── Global bank defaults ──────────────────────────────────────────────────
    # Rate limiting (applies to all banks unless overridden per-bank)
    rate_limit_requests: int = 60
    rate_limit_window: int = 60  # seconds

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_host: str = "redis"    # Docker service name; override with REDIS_HOST=localhost for local dev
    redis_port: int = 6379
    redis_db: int = 0             # db=0 : dedup keys + rate-limit counters (volatile-lru eviction OK)
    redis_stream_db: int = 1      # db=1 : webhook:batches stream (NO eviction — keys have no TTL)
    redis_password: str = ""

    # ── Logging ─────────────────────────────────────────────────────────
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
