"""
Redis Streams publisher — publishes a persisted batch to the stream.

Stream:   webhook:batches
Fields:   bank_id, batch_id, received_at, payload (full JSON string)

The worker service consumes this stream with XREADGROUP, inserts to Postgres,
then ACKs. Messages stay in the PEL until ACKed, so a worker crash leaves them
pending for the next run to reclaim via XAUTOCLAIM.

MAXLEN ~10000 keeps Redis memory bounded (older entries trimmed once consumed).

Fail-open: if Redis is down the webhook still processes and the file is on disk.
A clear ERROR log is emitted so an operator knows the batch needs manual replay.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import redis
import structlog

from app.config.settings import settings

logger = structlog.get_logger()

STREAM_NAME = "webhook:batches"
STREAM_MAXLEN = 10_000


class StreamPublisher:
    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None
        self._connect()

    def _connect(self) -> None:
        try:
            client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_stream_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            client.ping()
            self._redis = client
            logger.info("StreamPublisher: Redis connected", host=settings.redis_host)
        except Exception as exc:
            logger.warning(
                "StreamPublisher: Redis unavailable, stream publishing disabled",
                error=str(exc),
            )
            self._redis = None

    def publish(self, payload: Dict[str, Any]) -> Optional[str]:
        """
        Publish a persisted batch payload dict to the Redis Stream.

        Returns the stream entry ID on success, None on failure.
        Failure is non-fatal — the batch is already saved to disk.
        """
        if not self._redis:
            logger.error(
                "StreamPublisher: Redis unavailable — batch NOT queued for DB insert. "
                "Manual replay required.",
                batch_id=payload.get("batch_id"),
                bank_id=payload.get("bank_id"),
            )
            return None

        try:
            entry_id = self._redis.xadd(
                STREAM_NAME,
                {
                    "bank_id": payload.get("bank_id", ""),
                    "batch_id": payload.get("batch_id", ""),
                    "received_at": payload.get("received_at", ""),
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            logger.debug(
                "StreamPublisher: published",
                stream=STREAM_NAME,
                entry_id=entry_id,
                batch_id=payload.get("batch_id"),
                bank_id=payload.get("bank_id"),
            )
            return entry_id
        except Exception as exc:
            logger.error(
                "StreamPublisher: publish failed — batch NOT queued for DB insert. "
                "Manual replay required.",
                error=str(exc),
                batch_id=payload.get("batch_id"),
                bank_id=payload.get("bank_id"),
            )
            return None


# Singleton
stream_publisher = StreamPublisher()
