"""
Redis Sliding-Window Rate Limit Middleware
Logs all request headers + client info at INFO level on every request
to help identify what upstream senders transmit.
"""
import re
import time

import redis
import structlog
from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.banks.registry import bank_registry
from app.config.settings import settings

rate_limit_total = Counter(
    "rate_limit_total",
    "Total rate limit events",
    ["bank_id", "status"],  # status = allowed | exceeded
)

logger = structlog.get_logger(__name__)

_SKIP_PATHS = {"/health", "/metrics"}
_WEBHOOK_RE = re.compile(r"^/webhook/([^/]+)")


def _extract_client_id(request: Request) -> str:
    """Best-effort client identifier for rate-limit bucketing."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()

    xri = request.headers.get("x-real-ip", "")
    if xri:
        return xri.strip()

    if request.client:
        return request.client.host

    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._redis: redis.Redis | None = None
        try:
            self._redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            self._redis.ping()
            logger.info("rate_limit.redis_connected", host=settings.redis_host, port=settings.redis_port)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rate_limit.redis_unavailable", error=str(exc))
            self._redis = None

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # ── DEBUG: log all headers + client info on every request ──────────
        logger.debug(
            "rate_limit.request_received",
            path=path,
            method=request.method,
            all_headers=dict(request.headers),
            client_host=request.client.host if request.client else None,
            client_port=request.client.port if request.client else None,
        )

        # Pass health / metrics through immediately
        if path in _SKIP_PATHS:
            return await call_next(request)

        client_id = _extract_client_id(request)

        # ── Global kill-switch ──────────────────────────────────────────────
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Resolve rate-limit parameters
        match = _WEBHOOK_RE.match(path)
        if match:
            bank_id = match.group(1)
            try:
                handler = bank_registry.get(bank_id)
            except KeyError:
                # Let the IP/signature middlewares surface the 404
                return await call_next(request)
            # Per-bank kill-switch
            if not handler.config.rate_limit_enabled:
                rate_limit_total.labels(bank_id=bank_id, status="allowed").inc()
                return await call_next(request)
            max_requests = handler.config.rate_limit_requests
            window = handler.config.rate_limit_window
            key_prefix = f"ratelimit:{bank_id}:{client_id}"
        else:
            bank_id = "default"
            max_requests = settings.rate_limit_requests
            window = settings.rate_limit_window
            key_prefix = f"ratelimit:default:{client_id}"

        window_start = int(time.time()) // window
        redis_key = f"{key_prefix}:{window_start}"

        # ── Redis sliding-window check ──────────────────────────────────────
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(redis_key)
                pipe.expire(redis_key, window * 2)
                results = pipe.execute()
                current_count = results[0]

                logger.debug(
                    "rate_limit.counter",
                    redis_key=redis_key,
                    count=current_count,
                    max_requests=max_requests,
                    client_id=client_id,
                )

                if current_count > max_requests:
                    retry_after = window_start * window + window - int(time.time())
                    logger.warning(
                        "rate_limit.exceeded",
                        client_id=client_id,
                        bank_id=bank_id,
                        count=current_count,
                        max_requests=max_requests,
                        retry_after=retry_after,
                    )
                    rate_limit_total.labels(bank_id=bank_id, status="exceeded").inc()
                    return JSONResponse(
                        status_code=429,
                        content={
                            "success": False,
                            "message": "Rate limit exceeded",
                            "retry_after": retry_after,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                # Fail open — Redis is down; allow the request through
                logger.warning("rate_limit.redis_error_fail_open", error=str(exc), client_id=client_id)
        else:
            logger.warning("rate_limit.no_redis_fail_open", client_id=client_id)

        rate_limit_total.labels(bank_id=bank_id, status="allowed").inc()
        return await call_next(request)
