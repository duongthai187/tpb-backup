"""
Bank-agnostic deduplication service backed by Redis.

Key format:  dedup:{bank_id}:{env}:{transaction_id}
TTL:         7 days
Fallback:    in-memory set (used only when Redis is unreachable)
"""
from __future__ import annotations

import redis
import structlog
from typing import Optional, Set

from app.config.settings import settings

logger = structlog.get_logger()

_DEDUP_TTL = 7 * 24 * 3600  # 7 days


class DedupService:
    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None
        self._memory: Set[str] = set()
        self._connect()

    def _connect(self) -> None:
        try:
            client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            client.ping()
            self._redis = client
            logger.info("DedupService: Redis connected", host=settings.redis_host)
        except Exception as exc:
            logger.warning("DedupService: Redis unavailable, using in-memory fallback", error=str(exc))
            self._redis = None

    # ── Public API ────────────────────────────────────────────────────────────

    def is_duplicate(self, bank_id: str, transaction_id: str, is_uat: bool = False) -> bool:
        key = self._key(bank_id, transaction_id, is_uat)
        if self._redis:
            try:
                return bool(self._redis.exists(key))
            except Exception as exc:
                logger.warning("DedupService: Redis check failed, using memory", error=str(exc))
        return key in self._memory

    def mark_processed(self, bank_id: str, transaction_id: str, is_uat: bool = False) -> None:
        key = self._key(bank_id, transaction_id, is_uat)
        if self._redis:
            try:
                self._redis.setex(key, _DEDUP_TTL, "1")
                return
            except Exception as exc:
                logger.warning("DedupService: Redis mark failed, using memory", error=str(exc))
        self._memory.add(key)

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _key(bank_id: str, transaction_id: str, is_uat: bool) -> str:
        env = "uat" if is_uat else "prod"
        return f"dedup:{bank_id}:{env}:{transaction_id}"


# Singleton used by CoreProcessor and tests
dedup_service = DedupService()
