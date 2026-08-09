# Notify Service — Telegram Channel
# =========================================
# Reads webhook transactions from Redis Stream (S1) and sends
# formatted notifications to a Telegram chat.
#
# Usage:
#   TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python3 main.py
#
# Env vars:
#   REDIS_URL           — Redis connection (default: redis://192.168.255.71:6379/0)
#   STREAM_NAME         — Redis Stream name (default: webhook:batches)
#   TELEGRAM_BOT_TOKEN  — Telegram Bot token from @BotFather
#   TELEGRAM_CHAT_ID    — Target chat ID (group, channel, or user)
#   NOTIFY_TYPES        — Comma-separated trans types (default: C,D)
"""
Notify Service – lightweight Telegram notifier via Redis Streams.

Architecture:
  S1 Redis Stream ──► Notify Service ──► Telegram Bot API

Best practices implemented:
  • Independent consumer group → never interferes with pg-writers
  • Rate limiting (≤25 msg/s) → respects Telegram 30 msg/s limit
  • Exponential backoff retry (3x) → transient failures auto-recover
  • Poison pill → DLQ stream → malformed messages quarantined
  • XAUTOCLAIM → crash recovery, no lost messages
  • Health check endpoint (:8080/health) → monitoring
  • Structured logging (structlog) → observability
  • Channel interface → add Email/Zalo/Webhook via BaseChannel
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL", "redis://192.168.255.71:6379/1")
STREAM_NAME = os.getenv("STREAM_NAME", "webhook:batches")
DLQ_STREAM = os.getenv("DLQ_STREAM", "notify:dlq")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "notify-telegram")
CONSUMER_NAME = os.getenv("CONSUMER_NAME", "notifier-1")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))
BLOCK_MS = int(os.getenv("BLOCK_MS", "5000"))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8080"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NOTIFY_TYPES = set(
    t.strip().upper() for t in os.getenv("NOTIFY_TYPES", "C,D").split(",") if t.strip()
)

# Telegram rate limit: 30 msg/s official; stay at 25 for safety margin
TELEGRAM_RATE_LIMIT = 25  # max sends per second
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF = [1, 5, 15]  # seconds between retries (must match MAX_RETRIES)

# ═══════════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════════

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
LOG = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class NotifyPayload:
    bank_id: str
    batch_id: str
    transaction_id: str
    amount: float
    trans_type: str          # "C" or "D"
    src_account_number: str
    trans_desc: str
    trans_time: str
    notice_datetime: str
    received_at: str
    ofs_account_number: str
    ofs_account_name: str
    balance_available: str
    payload_timestamp: str

    @classmethod
    def from_transaction(cls, tx: Dict[str, Any], batch: Dict[str, Any]) -> "NotifyPayload":
        return cls(
            bank_id=batch.get("bank_id", ""),
            batch_id=batch.get("batch_id", ""),
            transaction_id=str(tx.get("transaction_id", "")),
            amount=float(tx.get("amount", 0)),
            trans_type=str(tx.get("trans_type", "")).upper(),
            src_account_number=str(tx.get("src_account_number", "")),
            trans_desc=str(tx.get("trans_desc", "")),
            trans_time=str(tx.get("trans_time", "")),
            notice_datetime=str(tx.get("notice_date_time", "")),
            received_at=str(batch.get("received_at", "")),
            ofs_account_number=str(tx.get("ofs_account_number", "")),
            ofs_account_name=str(tx.get("ofs_account_name", "")),
            balance_available=str(tx.get("balance_available", "")),
            payload_timestamp=str(batch.get("timestamp", batch.get("payload_timestamp", ""))),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Rate Limiter (token bucket)
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Token bucket rate limiter — thread-safe for asyncio."""

    def __init__(self, max_rate: float) -> None:
        self._max_rate = max_rate
        self._tokens = max_rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_rate, self._tokens + elapsed * self._max_rate)
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self._max_rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics (simple in-memory counters)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Metrics:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    dlq: int = 0
    errors: int = 0

    def snapshot(self) -> Dict[str, int]:
        return {
            "notify_sent_total": self.sent,
            "notify_failed_total": self.failed,
            "notify_skipped_total": self.skipped,
            "notify_dlq_total": self.dlq,
            "notify_errors_total": self.errors,
        }


METRICS = Metrics()


# ═══════════════════════════════════════════════════════════════════════════════
# Channel Interface
# ═══════════════════════════════════════════════════════════════════════════════

class BaseChannel(ABC):
    """Abstract channel – implement for each notification target."""

    @abstractmethod
    async def send(self, payload: NotifyPayload) -> bool:
        """Send notification. Returns True on success."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Telegram Channel
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramChannel(BaseChannel):
    """Sends formatted transaction notifications to a Telegram chat.
    
    Respects Telegram rate limits via token bucket (≤25 msg/s).
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._api = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15))
        self._limiter = RateLimiter(TELEGRAM_RATE_LIMIT)

    async def send(self, p: NotifyPayload) -> bool:
        emoji = "🟢" if p.trans_type == "C" else "🔴"
        type_label = "NHẬN VỀ (C)" if p.trans_type == "C" else "CHUYỂN ĐI (D)"
        amount_fmt = f"{p.amount:,.0f} VND"

        if p.trans_type == "C":
            sender = f"`{p.ofs_account_number}` — {p.ofs_account_name}"
            receiver = f"`{p.src_account_number}` (tài khoản nhận)"
        else:
            sender = f"`{p.src_account_number}` (tài khoản gửi)"
            receiver = f"`{p.ofs_account_number}` — {p.ofs_account_name}"

        text = (
            f"{emoji} *{type_label}*\n"
            f"├ Số tiền: `{amount_fmt}`\n"
            f"├ Người gửi: {sender}\n"
            f"├ Người nhận: {receiver}\n"
            f"├ Mô tả: `{p.trans_desc}`\n"
            f"└ Thời gian GD: `{p.payload_timestamp}`\n"
            f"#tx\\_`{p.transaction_id}`"
        )

        await self._limiter.acquire()  # ← rate limit (best practice)

        try:
            resp = await self._client.post(
                f"{self._api}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
            if resp.status_code == 200:
                LOG.debug("telegram.sent", tx_id=p.transaction_id)
                METRICS.sent += 1
                return True
            elif resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                LOG.warning("telegram.rate_limited", tx_id=p.transaction_id, retry_after=retry_after)
                await asyncio.sleep(int(retry_after))
                return False
            else:
                LOG.warning("telegram.failed", tx_id=p.transaction_id, status=resp.status_code, body=resp.text[:200])
                METRICS.failed += 1
                return False
        except Exception as exc:
            LOG.error("telegram.error", tx_id=p.transaction_id, error=str(exc))
            METRICS.errors += 1
            return False

    async def close(self) -> None:
        await self._client.aclose()


# ═══════════════════════════════════════════════════════════════════════════════
# Redis Stream Consumer
# ═══════════════════════════════════════════════════════════════════════════════

class StreamConsumer:
    """Reads from Redis Stream using consumer group, dispatches to channel.
    
    Key behaviors:
      - XREADGROUP ">" for new messages (no replay)
      - XAUTOCLAIM for crash recovery
      - Exponential retry via PEL (don't ACK on failure)
      - Poison pill → DLQ after MAX_RETRIES
    """

    def __init__(self, redis: Redis, channel: BaseChannel) -> None:
        self._redis = redis
        self._channel = channel
        self._retry_count: Dict[bytes, int] = {}  # msg_id → attempt count

    async def setup(self) -> None:
        """Ensure consumer group exists (idempotent)."""
        try:
            await self._redis.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="$", mkstream=True)
            LOG.info("consumer_group.created", stream=STREAM_NAME, group=CONSUMER_GROUP)
        except RedisError as exc:
            if "BUSYGROUP" in str(exc):
                LOG.info("consumer_group.exists", stream=STREAM_NAME, group=CONSUMER_GROUP)
            else:
                raise

    async def run(self) -> None:
        """Main loop: XREADGROUP → parse → dispatch → XACK (or retry/DLQ)."""
        await self.setup()
        LOG.info("consumer.started", stream=STREAM_NAME, group=CONSUMER_GROUP, consumer=CONSUMER_NAME)

        while True:
            try:
                await self._reclaim()
                result = await self._redis.xreadgroup(
                    CONSUMER_GROUP, CONSUMER_NAME,
                    {STREAM_NAME: ">"},
                    block=BLOCK_MS,
                    count=BATCH_SIZE,
                )
                if not result:
                    continue

                for _stream_name, entries in result:
                    for msg_id, fields in entries:
                        await self._process_one(msg_id, fields)

            except RedisError as exc:
                LOG.error("consumer.redis_error", error=str(exc))
                await asyncio.sleep(1)
            except Exception as exc:
                LOG.error("consumer.unexpected_error", error=str(exc), exc_info=True)
                await asyncio.sleep(1)

    async def _reclaim(self) -> None:
        """XAUTOCLAIM idle messages from crashed consumers (best practice)."""
        try:
            claimed = await self._redis.xautoclaim(
                STREAM_NAME, CONSUMER_GROUP, CONSUMER_NAME,
                min_idle_time=30_000,
                count=10,
            )
            for msg_id, fields in claimed[1]:
                LOG.info("consumer.reclaimed", msg_id=msg_id)
                await self._process_one(msg_id, fields)
        except RedisError:
            pass

    async def _process_one(self, msg_id: bytes, fields: Dict[bytes, bytes]) -> None:
        """Process a single stream message with retry + DLQ."""
        try:
            raw_payload = fields.get(b"payload", b"{}")
            batch = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError) as exc:
            LOG.error("consumer.bad_json", msg_id=msg_id, error=str(exc))
            await self._to_dlq(msg_id, fields, f"JSON parse error: {exc}")
            await self._redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
            return

        try:
            transactions = batch.get("transactions", [])
            if not transactions:
                await self._redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
                return

            for tx in transactions:
                p = NotifyPayload.from_transaction(tx, batch)
                if p.trans_type not in NOTIFY_TYPES:
                    METRICS.skipped += 1
                    continue

                success = await self._send_with_retry(p)
                if not success:
                    self._retry_count[msg_id] = self._retry_count.get(msg_id, 0) + 1
                    if self._retry_count[msg_id] >= MAX_RETRIES:
                        LOG.error("consumer.poison_pill", msg_id=msg_id, tx_id=p.transaction_id)
                        await self._to_dlq(msg_id, fields, f"Failed after {MAX_RETRIES} retries")
                        await self._redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
                        METRICS.dlq += 1
                        return
                    # Stay in PEL → will be reclaimed/retried
                    return

            # All transactions processed → ACK
            await self._redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
            self._retry_count.pop(msg_id, None)

        except Exception as exc:
            LOG.error("consumer.process_error", msg_id=msg_id, error=str(exc), exc_info=True)
            await self._to_dlq(msg_id, fields, f"Unexpected error: {exc}")
            await self._redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
            METRICS.dlq += 1

    async def _send_with_retry(self, p: NotifyPayload) -> bool:
        """Exponential backoff retry for transient failures (best practice)."""
        for attempt in range(MAX_RETRIES):
            success = await self._channel.send(p)
            if success:
                return True
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF[attempt]
                LOG.info("notify.retry", tx_id=p.transaction_id, attempt=attempt + 1, delay=delay)
                await asyncio.sleep(delay)
        return False

    async def _to_dlq(self, msg_id: bytes, fields: Dict[bytes, bytes], reason: str) -> None:
        """Move failed message to Dead Letter Queue stream (best practice)."""
        try:
            await self._redis.xadd(DLQ_STREAM, {
                b"original_stream": STREAM_NAME.encode(),
                b"original_id": msg_id,
                b"failed_at": str(int(time.time())).encode(),
                b"reason": reason.encode()[:500],
                b"payload": fields.get(b"payload", b"{}"),
            }, maxlen=10_000, approximate=True)
            LOG.info("consumer.dlq", msg_id=msg_id, reason=reason)
        except RedisError as exc:
            LOG.error("consumer.dlq_failed", msg_id=msg_id, error=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Health Check (lightweight HTTP via asyncio)
# ═══════════════════════════════════════════════════════════════════════════════

async def health_check_server(port: int = 8080) -> None:
    """Minimal HTTP health endpoint — no extra dependency (best practice)."""
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            body = json.dumps({
                "status": "healthy",
                "consumer": CONSUMER_NAME,
                "group": CONSUMER_GROUP,
                **METRICS.snapshot(),
            })
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
                f"{body}"
            )
            writer.write(response.encode())
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handler, "0.0.0.0", port)
    LOG.info("health_check.started", port=port)
    async with server:
        await server.serve_forever()


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.error("missing_config", hint="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars")
        sys.exit(1)

    redis = Redis.from_url(
        REDIS_URL,
        decode_responses=False,
        socket_connect_timeout=10,
        socket_timeout=None,          # ← allow XREADGROUP BLOCK without socket timeout
        socket_keepalive=True,
    )
    channel = TelegramChannel(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    consumer = StreamConsumer(redis, channel)

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        LOG.info("shutdown.requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run consumer + health check concurrently
    tasks = [
        asyncio.create_task(consumer.run()),
        asyncio.create_task(health_check_server(HEALTH_PORT)),
    ]

    await stop_event.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    await channel.close()
    await redis.aclose()
    LOG.info("shutdown.complete")


if __name__ == "__main__":
    asyncio.run(main())
