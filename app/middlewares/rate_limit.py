import time
import redis
from typing import Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import structlog
import asyncio

from app.config.settings import settings

logger = structlog.get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):

    def __init__(self, app):
        super().__init__(app)
        self.redis_client = None
        self._initialize_redis()
    
    def _initialize_redis(self):
        try:
            self.redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password if settings.redis_password else None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            # Test connection
            self.redis_client.ping()
            logger.info("Khởi tạo kết nối Redis thành công (_initialize_redis)", 
                       host=settings.redis_host, 
                       port=settings.redis_port)
            
        except Exception as e:
            logger.error("Khởi tạo kết nối Redis thất bại (_initialize_redis)", error=str(e))
            self.redis_client = None
    
    def _get_client_identifier(self, request: Request) -> str:

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        
        forwarded = request.headers.get("Forwarded")
        if forwarded:
            for part in forwarded.split(';'):
                if part.strip().startswith('for='):
                    return part.strip().split('=')[1].strip('"')
        
        return request.client.host if request.client else "unknown"
    
    def _get_redis_key(self, client_id: str, window_start: int) -> str:
        return f"RateLimit:{client_id}:{window_start}"
    
    async def _check_rate_limit_redis(self, client_id: str) -> tuple[bool, int, int]:
        if not self.redis_client:
            # If Redis is not available, allow request but log warning
            logger.warning("Redis không khả dụng, cho phép yêu cầu (_check_rate_limit_redis)", client_id=client_id)
            return True, 0, int(time.time()) + settings.rate_limit_window
        
        current_time = int(time.time())
        window_start = current_time // settings.rate_limit_window * settings.rate_limit_window
        redis_key = self._get_redis_key(client_id, window_start)
        
        try:
            # Use Redis pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            
            # Increment counter
            pipe.incr(redis_key)
            
            # Set expiration if key is new
            pipe.expire(redis_key, settings.rate_limit_window * 2)  # Keep for 2 windows
            
            # Execute pipeline
            results = pipe.execute()
            current_count = results[0]
            
            reset_time = window_start + settings.rate_limit_window
            is_allowed = current_count <= settings.rate_limit_requests
            
            logger.info("Kiểm tra giới hạn tốc độ (Redis) (_check_rate_limit_redis)",
                       client_id=client_id,
                       current_count=current_count,
                       limit=settings.rate_limit_requests,
                       is_allowed=is_allowed,
                       reset_time=reset_time)
            
            return is_allowed, current_count, reset_time
            
        except Exception as e:
            logger.error("Lỗi giới hạn tốc độ (Redis) (_check_rate_limit_redis)", error=str(e))
            # On Redis error, allow request but log warning
            return True, 0, int(time.time()) + settings.rate_limit_window
    
    # In-memory fallback for when Redis is unavailable
    _memory_store = {}
    
    def _cleanup_memory_store(self):
        current_time = int(time.time())
        keys_to_remove = []
        
        for key in self._memory_store:
            if key.endswith(f"_reset"):
                continue
                
            reset_time = self._memory_store.get(f"{key}_reset", 0)
            if current_time > reset_time:
                keys_to_remove.append(key)
                keys_to_remove.append(f"{key}_reset")
        
        for key in keys_to_remove:
            self._memory_store.pop(key, None)
    
    async def _check_rate_limit_memory(self, client_id: str) -> tuple[bool, int, int]:
        current_time = int(time.time())
        window_start = current_time // settings.rate_limit_window * settings.rate_limit_window
        reset_time = window_start + settings.rate_limit_window
        
        # Cleanup old entries periodically
        if len(self._memory_store) > 1000:  # Cleanup when store gets large
            self._cleanup_memory_store()
        
        key = f"memory_rate_limit:{client_id}:{window_start}"
        reset_key = f"{key}_reset"
        
        # Initialize if not exists
        if key not in self._memory_store:
            self._memory_store[key] = 0
            self._memory_store[reset_key] = reset_time
        
        # Increment counter
        self._memory_store[key] += 1
        current_count = self._memory_store[key]
        
        is_allowed = current_count <= settings.rate_limit_requests
        
        logger.info("Kiểm tra giới hạn tốc độ (Memory) (_check_rate_limit_memory)",
                   client_id=client_id,
                   current_count=current_count,
                   limit=settings.rate_limit_requests,
                   is_allowed=is_allowed,
                   reset_time=reset_time)
        
        return is_allowed, current_count, reset_time
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health and metrics endpoints
        if request.url.path in ["/health", "/metrics"]:
            return await call_next(request)
        
        try:
            # Get client identifier
            client_id = self._get_client_identifier(request)
            
            # Check rate limit (prefer Redis, fallback to memory)
            if self.redis_client:
                is_allowed, current_count, reset_time = await self._check_rate_limit_redis(client_id)
            else:
                is_allowed, current_count, reset_time = await self._check_rate_limit_memory(client_id)
            
            # Add rate limit headers
            def add_rate_limit_headers(response):
                response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_requests)
                response.headers["X-RateLimit-Remaining"] = str(max(0, settings.rate_limit_requests - current_count))
                response.headers["X-RateLimit-Reset"] = str(reset_time)
                response.headers["X-RateLimit-Window"] = str(settings.rate_limit_window)
                return response
            
            # Check if request is allowed
            if not is_allowed:
                logger.warning("Vượt quá giới hạn tốc độ (dispatch)",
                              client_id=client_id,
                              current_count=current_count,
                              limit=settings.rate_limit_requests,
                              path=request.url.path)
                
                response = JSONResponse(
                    status_code=200,
                    content={
                        "batchId": "unknown",
                        "code": "429",
                        "message": "Vượt quá giới hạn tốc độ",
                        "data": []
                    }
                )
                
                return add_rate_limit_headers(response)
            
            # Request is allowed, proceed
            response = await call_next(request)
            return add_rate_limit_headers(response)
            
        except Exception as e:
            logger.error("Lỗi kiểm tra Rate limit", error=str(e), exc_info=True)
            return JSONResponse(
                status_code=200,
                content={
                    "batchId": "unknown",
                    "code": "500", 
                    "message": "Lỗi kiểm tra Rate limit",
                    "data": []
                }
            )