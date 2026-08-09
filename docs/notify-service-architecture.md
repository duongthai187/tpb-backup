# Notify Service — Architecture Design

## 1. Tổng quan

```
                              ┌─────────────────────────────────┐
TPBank ─► Kong ─► S1 API ─►  │  Redis Stream "webhook:batches" │
                              └──────────┬──────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
               Consumer Group       Consumer Group       Consumer Group
               "pg-writers"         "notify-service"     "s2-reader" (tùy chọn)
               (S1 workers)         (S2 notify)          (S2 khác)
                    │                    │
                    ▼                    ▼
               Postgres (S1)      ┌─────────────────────┐
                    │             │  Notify Service (S2) │
                    ▼             │  ┌─────────────────┐│
               Replication        │  │ Channel Router   ││
                    │             │  │ ┌──────┐┌──────┐ ││
                    ▼             │  │ │Tele- ││Email │ ││
               Postgres (S2)      │  │ │gram  ││(Gmail)│ ││
                                  │  │ └──────┘└──────┘ ││
                                  │  │ ┌──────┐┌──────┐ ││
                                  │  │ │Zalo  ││Web-  │ ││
                                  │  │ │(OA)  ││hook  │ ││
                                  │  │ └──────┘└──────┘ ││
                                  │  └─────────────────┘│
                                  └─────────────────────┘
```

## 2. Tại sao Redis Streams (không phải Pub/Sub)?

| | Redis Pub/Sub | Redis Streams |
|---|---|---|
| **Delivery** | At-most-once | At-least-once (consumer group) |
| **Replay** | Không | Có (đọc lại từ ID cũ) |
| **Crash recovery** | Mất message | XAUTOCLAIM reclaim |
| **Nhiều consumer** | Mỗi consumer nhận hết | Consumer group chia đều |
| **Nhiều group** | Không hỗ trợ | Mỗi group đọc độc lập |

→ **Redis Streams là lựa chọn đúng** cho notify service: đảm bảo không mất notify, replay được nếu lỗi.

## 3. Inter-service Communication (S2 ↔ Service khác)

### Lựa chọn 1: Webhook HTTP (khuyến nghị)

```
Notify Service (S2) ──HTTP POST──► Target Service (192.168.255.x)
```

- **Ưu**: Đơn giản nhất, stateless, dễ scale, dễ retry
- **Nhược**: Không real-time bằng socket
- **Phù hợp**: Khi target service có sẵn HTTP endpoint

### Lựa chọn 2: WebSocket (persistent connection)

```
Notify Service (S2) ◄══WebSocket══► Target Service (192.168.255.x)
```

- **Ưu**: Real-time, 2 chiều, ít overhead
- **Nhược**: Cần quản lý connection state, reconnect logic
- **Phù hợp**: Khi cần push real-time + nhận ACK từ target

### Lựa chọn 3: Redis Stream (best practice) ⭐

```
Notify Service (S2) ──XADD──► Redis Stream "notify:outbox" (S2 Redis)
                                      │
Target Service ──XREADGROUP──► Consume notify của riêng nó
```

- **Ưu**: Target service tự consume theo tốc độ của nó, không cần HTTP/WS endpoint
- **Nhược**: Target service cần Redis client và consumer group
- **Phù hợp**: Khi target service có thể connect Redis

### Khuyến nghị: **Webhook HTTP + Redis Stream outbox**

```
                    ┌──────────────────────────┐
                    │   Notify Service (S2)     │
                    │                          │
Stream "batches" ──►│ 1. Parse message         │
(from S1 Redis)     │ 2. Match rules           │
                    │ 3. Route to channel      │
                    │                          │
                    │ ┌──────────┐ ┌─────────┐ │
                    │ │ Telegram │ │  Email  │ │
                    │ └──────────┘ └─────────┘ │
                    │ ┌──────────┐ ┌─────────┐ │
                    │ │  Zalo    │ │ Webhook │ │
                    │ └──────────┘ └────┬────┘ │
                    └───────────────────┼──────┘
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                    Service A     Service B     Service C
                    (192.168.x.x) (192.168.x.x) (192.168.x.x)
```

## 4. Cấu trúc thư mục

```
notify-service/
├── main.py                  # Entry point
├── config.py                # Settings (Redis, Telegram, Email, Zalo, Webhooks)
├── consumer.py              # Redis Stream consumer (XREADGROUP loop)
├── router.py                # Rule engine: match conditions → channels
├── channels/
│   ├── __init__.py
│   ├── base.py              # Abstract channel interface
│   ├── telegram.py          # Telegram Bot API
│   ├── email.py             # SMTP / Gmail API
│   ├── zalo.py              # Zalo OA API
│   └── webhook.py           # HTTP POST to target services
├── templates/               # Jinja2 message templates
│   ├── telegram.txt
│   ├── email.html
│   └── zalo.txt
├── retry.py                 # Retry + Dead Letter Queue (DLQ)
└── requirements.txt
```

## 5. Code Skeleton

### 5.1 Channel Interface (base.py)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class NotifyMessage:
    """Normalized message from stream → channel."""
    bank_id: str
    batch_id: str
    transaction_id: str
    amount: float
    trans_type: str          # "C" or "D"
    account_number: str
    trans_time: str
    trans_desc: str
    raw_payload: Dict[str, Any]

@dataclass
class NotifyResult:
    success: bool
    channel: str
    message_id: str
    error: Optional[str] = None

class BaseChannel(ABC):
    @abstractmethod
    async def send(self, msg: NotifyMessage) -> NotifyResult:
        """Send one notification. Return result for retry tracking."""
        ...
```

### 5.2 Webhook Channel (webhook.py)

```python
import aiohttp
from .base import BaseChannel, NotifyMessage, NotifyResult

class WebhookChannel(BaseChannel):
    def __init__(self, target_url: str, timeout: int = 10):
        self.target_url = target_url
        self.timeout = timeout

    async def send(self, msg: NotifyMessage) -> NotifyResult:
        payload = {
            "transaction_id": msg.transaction_id,
            "bank_id": msg.bank_id,
            "amount": msg.amount,
            "trans_type": msg.trans_type,
            "account_number": msg.account_number,
            "trans_time": msg.trans_time,
            "trans_desc": msg.trans_desc,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.target_url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    if resp.status in (200, 201, 204):
                        return NotifyResult(success=True, channel="webhook",
                                           message_id=msg.transaction_id)
                    return NotifyResult(success=False, channel="webhook",
                                       message_id=msg.transaction_id,
                                       error=f"HTTP {resp.status}")
        except Exception as e:
            return NotifyResult(success=False, channel="webhook",
                               message_id=msg.transaction_id, error=str(e))
```

### 5.3 Router (router.py)

```python
from typing import List
from .channels.base import BaseChannel, NotifyMessage

class NotifyRouter:
    """Match messages to channels based on rules."""

    def __init__(self):
        self.rules: List[tuple] = []  # (condition_fn, [channels])

    def add_rule(self, condition, channels: List[BaseChannel]):
        self.rules.append((condition, channels))

    async def route(self, msg: NotifyMessage) -> List:
        results = []
        for condition, channels in self.rules:
            if condition(msg):
                for ch in channels:
                    results.append(await ch.send(msg))
        return results

# Ví dụ rules
def all_transactions(msg: NotifyMessage) -> bool:
    return True

def high_value(msg: NotifyMessage) -> bool:
    return msg.amount >= 100_000_000  # > 100 triệu

def credit_only(msg: NotifyMessage) -> bool:
    return msg.trans_type == "C"
```

### 5.4 Consumer (consumer.py)

```python
import json
import asyncio
from redis.asyncio import Redis

class NotifyConsumer:
    def __init__(self, redis_url: str, stream: str, group: str, consumer: str):
        self.redis = Redis.from_url(redis_url)
        self.stream = stream
        self.group = group
        self.consumer = consumer

    async def setup(self):
        try:
            await self.redis.xgroup_create(
                self.stream, self.group, id="0", mkstream=True
            )
        except Exception:
            pass  # Group already exists

    async def run(self, router):
        await self.setup()
        while True:
            try:
                msgs = await self.redis.xreadgroup(
                    self.group, self.consumer,
                    {self.stream: ">"}, block=5000, count=10
                )
                for stream_name, entries in msgs:
                    for msg_id, fields in entries:
                        payload = json.loads(fields[b"payload"])
                        for tx in payload.get("transactions", []):
                            notify_msg = NotifyMessage(
                                bank_id=payload.get("bank_id", ""),
                                batch_id=payload.get("batch_id", ""),
                                transaction_id=tx.get("transaction_id", ""),
                                amount=float(tx.get("amount", 0)),
                                trans_type=tx.get("trans_type", ""),
                                account_number=tx.get("src_account_number", ""),
                                trans_time=tx.get("trans_time", ""),
                                trans_desc=tx.get("trans_desc", ""),
                                raw_payload=payload,
                            )
                            await router.route(notify_msg)
                        await self.redis.xack(self.stream, self.group, msg_id)
            except Exception:
                await asyncio.sleep(1)
```

## 6. Retry + Dead Letter Queue

```
Send fail → Retry (3 lần, exponential backoff)
                │
                ├── Success → XACK
                └── All fail → XADD "notify:dlq" (dead letter queue)
                                │
                                └── Admin review / manual retry
```

```python
class RetryHandler:
    MAX_RETRIES = 3
    BACKOFF = [1, 5, 30]  # seconds

    async def send_with_retry(self, channel: BaseChannel, msg: NotifyMessage):
        for attempt, delay in enumerate(self.BACKOFF):
            result = await channel.send(msg)
            if result.success:
                return result
            await asyncio.sleep(delay)
        # All retries failed → DLQ
        await self.redis.xadd("notify:dlq", {
            "channel": channel.__class__.__name__,
            "message": json.dumps(msg.raw_payload),
            "error": result.error,
            "failed_at": datetime.utcnow().isoformat(),
        })
        return result
```

## 7. Triển khai trên S2

```bash
# docker-compose.s2.yml thêm service mới
notify-service:
  build: ./notify-service
  container_name: tpb_notify
  restart: unless-stopped
  environment:
    REDIS_URL: redis://192.168.255.71:6379/0
    TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
    SMTP_HOST: smtp.gmail.com
    SMTP_PORT: 587
    SMTP_USER: ${SMTP_USER}
    SMTP_PASS: ${SMTP_PASS}
    ZALO_OA_TOKEN: ${ZALO_OA_TOKEN}
    WEBHOOK_URLS: http://192.168.255.x:8080/notify,http://192.168.255.y:9000/hook
  networks:
    - webhook-net
```

## 8. So sánh WebSocket vs HTTP cho inter-service

| Tiêu chí | WebSocket | HTTP Webhook |
|---|---|---|
| **Độ phức tạp** | Cao (quản lý connection, reconnect) | Thấp |
| **Real-time** | Tốt | Gần real-time (~ms) |
| **Reliability** | Cần tự implement ack/retry | HTTP status code là đủ |
| **Scale** | Khó (stateful connection) | Dễ (stateless, load balancer) |
| **Firewall** | Có thể bị chặn | Thường được phép |
| **Monitoring** | Khó hơn | Dễ (HTTP status, latency) |

**Khuyến nghị**: Dùng **HTTP Webhook** cho inter-service. WebSocket chỉ nên dùng nếu cần:
- Push real-time < 100ms
- Giao tiếp 2 chiều (target service gửi lại ACK/status)
- Connection count thấp (< 100)

## 9. Tổng kết

```
S1 Redis Stream ──► S2 Notify Service ──┬── Telegram Bot
                                        ├── Gmail SMTP
                                        ├── Zalo OA
                                        └── HTTP Webhook → Service A, B, C
                                                          (192.168.255.x)
```

- **Event source**: Redis Streams (sẵn có, không thêm infra)
- **Consumer**: Independent consumer group, không ảnh hưởng workers
- **Channels**: Plugin-based, dễ thêm channel mới
- **Retry**: Built-in, 3 lần + DLQ
- **Inter-service**: HTTP Webhook (đơn giản, reliable, dễ scale)
- **Không cần WebSocket** trừ khi có yêu cầu real-time cực thấp