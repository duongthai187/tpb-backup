"""
Redis Streams consumer — XREADGROUP loop with crash-recovery via XAUTOCLAIM.

Flow:
  1. On startup: XGROUP CREATE (idempotent, MKSTREAM creates stream if absent)
  2. Reclaim any messages idle > claim_idle_ms from previous crashed consumers
  3. Loop: XREADGROUP ">" → process → XACK
  4. On process failure: do NOT ack → message stays in PEL → reclaimed on next restart

XAUTOCLAIM requires Redis 6.2+ (our stack uses redis:7-alpine — OK).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import redis
import redis.exceptions

from .config import settings

if TYPE_CHECKING:
    from .processor import MessageProcessor

LOGGER = logging.getLogger(__name__)

STREAM = settings.stream_name
GROUP = settings.consumer_group
CONSUMER = settings.consumer_name


class StreamConsumer:
    def __init__(self, processor: "MessageProcessor") -> None:
        self.processor = processor
        self._stop = False
        self._redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_stream_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=10,
            retry_on_timeout=True,
        )

    def ensure_group(self) -> None:
        """Create consumer group if it doesn't exist yet (idempotent)."""
        try:
            self._redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            LOGGER.info("Created consumer group '%s' on stream '%s'", GROUP, STREAM)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                LOGGER.info("Consumer group '%s' already exists", GROUP)
            else:
                raise

    def run(self) -> None:
        self.ensure_group()
        LOGGER.info(
            "Consumer started | stream=%s group=%s consumer=%s",
            STREAM, GROUP, CONSUMER,
        )

        # Reclaim unACKed messages from crashed previous runs
        self._reclaim_pending()

        while not self._stop:
            try:
                response = self._redis.xreadgroup(
                    GROUP,
                    CONSUMER,
                    {STREAM: ">"},
                    count=settings.batch_size,
                    block=settings.block_ms,
                )
                if not response:
                    # block timeout — loop again
                    continue
                for _stream_name, entries in response:
                    for entry_id, fields in entries:
                        self._handle(entry_id, fields)

            except redis.exceptions.ConnectionError as exc:
                LOGGER.error("Redis connection error — retrying in 5s: %s", exc)
                time.sleep(5)
            except redis.exceptions.TimeoutError:
                # block timeout returned as exception on some redis-py versions
                continue
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.error("Unexpected consumer loop error: %s", exc, exc_info=True)
                time.sleep(1)

    def _handle(self, entry_id: str, fields: dict) -> None:
        try:
            self.processor.process(fields)
            self._redis.xack(STREAM, GROUP, entry_id)
            LOGGER.debug("ACKed %s", entry_id)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error(
                "Processing failed for %s — will retry on next reclaim: %s",
                entry_id, exc,
            )
            # Intentionally NOT ACKing — stays in PEL

    def _reclaim_pending(self) -> None:
        """
        Reclaim messages idle > claim_idle_ms.
        Handles messages left over from crashed consumers.
        """
        try:
            result = self._redis.xautoclaim(
                STREAM,
                GROUP,
                CONSUMER,
                min_idle_time=settings.claim_idle_ms,
                start_id="0-0",
                count=100,
            )
            # result = (next_start_id, [(id, fields), ...], [deleted_ids])
            claimed_entries = result[1] if result and len(result) > 1 else []
            if claimed_entries:
                LOGGER.info("Reclaimed %d pending messages", len(claimed_entries))
                for entry_id, fields in claimed_entries:
                    self._handle(entry_id, fields)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Could not reclaim pending messages: %s", exc)

    def stop(self) -> None:
        self._stop = True
