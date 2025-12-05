import base64
import json
import hashlib
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature
import structlog

from app.config.settings import settings

logger = structlog.get_logger()


class SignatureVerificationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.bank_public_key = None
        self._load_bank_public_key()
    
    def _load_bank_public_key(self):
        try:
            self.bank_public_key = settings.load_bank_public_key()
            logger.info("Tải mã công khai publickey_bank thành công", key_size=self.bank_public_key.key_size)
        except FileNotFoundError as e:
            logger.error("Không tìm thấy khóa công khai xác thực signature", error=str(e))
            self.bank_public_key = None
        except Exception as e:
            logger.error("Lỗi khi tải khóa công khai xác thực signature", error=str(e))
            self.bank_public_key = None
    
    async def dispatch(self, request: Request, call_next):
        # Only verify signature for webhook endpoints
        if not request.url.path.startswith("/webhook/"):
            return await call_next(request)
        
        if request.method != "POST":
            return await call_next(request)
        
        try:
            body = await request.body()
            
            if not body:
                logger.error("Không đọc được body request")
                return JSONResponse(
                    status_code=200,
                    content={
                        "batchId": "unknown",
                        "code": "400",
                        "message": "Không đọc được body request",
                        "data": []
                    }
                )
            
            try:
                payload = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError as e:
                logger.error("Lỗi khi phân tích JSON", error=str(e))
                return JSONResponse(
                    status_code=200,
                    content={
                        "batchId": "unknown",
                        "code": "400", 
                        "message": "Lỗi khi phân tích JSON",
                        "data": []
                    }
                )
            
            batch_id = payload.get('batchId', 'unknown')
            
            # Extract signature
            signature = payload.get('signature')
            if not signature:
                logger.error("Không tìm thấy chữ ký")
                return JSONResponse(
                    status_code=200,
                    content={
                        "batchId": batch_id,
                        "code": "401",
                        "message": "Không tìm thấy chữ ký", 
                        "data": []
                    }
                )
            
            # Create payload for signature verification (exclude signature field)
            payload_for_verification = {k: v for k, v in payload.items() if k != 'signature'}
            
            # Verify signature
            if not await self._verify_signature(payload_for_verification, signature):
                logger.error(
                    "Signature không hợp lệ (dispatch)",
                    batch_id=batch_id
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "batchId": batch_id,
                        "code": "401",
                        "message": "Chữ ký không hợp lệ (dispatch)",
                        "data": []
                    }
                )
            
            logger.info(
                "Signature hợp lệ, tiếp tục xử lý request (dispatch)",
                transaction_id=payload.get('transaction_id', 'unknown')
            )
            
            # Create new request with body for downstream processing
            async def receive():
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False
                }
            
            request._receive = receive
            
            return await call_next(request)
            
        except Exception as e:
            logger.error("Signature verification error", error=str(e), exc_info=True)
            return JSONResponse(
                status_code=200,
                content={
                    "batchId": payload.get("batchId", "unknown") if payload else "unknown",
                    "code": "500",
                    "message": "Signature verification error",
                    "data": []
                }
            )
    
    async def _verify_signature(self, payload: dict, signature: str) -> bool:
        if not self.bank_public_key:
            logger.error("Không có khóa công khai để xác thực chữ ký (_verify_signature)")
            return False
        
        try:
            canonical_string = self._create_canonical_string(payload)
            # print(f"   canonical_string: '{canonical_string}'")
            # print(f"   base64_signature_length: {len(signature)} chars")
            # print(f"   base64_signature: {signature[:50]}...")
            # Decode base64 signature
            signature_bytes = base64.b64decode(signature)
            # print(f"   raw_signature_length: {len(signature_bytes)} bytes")
            # print(f"   raw_signature_hex: {signature_bytes.hex()[:50]}...")
            # Verify signature using SHA512withRSA
            self.bank_public_key.verify(
                signature_bytes,
                canonical_string.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA512()
            )
            
            logger.info("Verification Signature Successful")
            return True
            
        except InvalidSignature:
            logger.error("Signature verification failed: Invalid signature (_verify_signature)")
            return False
        except Exception as e:
            logger.error("Signature verification failed: Unexpected error (_verify_signature)", error=str(e))
            return False
    
    def _create_canonical_string(self, payload: dict) -> str:
        # Extract required fields according to bank spec
        source_app_id = payload.get('sourceAppId', '')
        batch_id = payload.get('batchId', '')
        timestamp = payload.get('timestamp', '')
        
        # Create canonical string by direct concatenation
        canonical_string = str(source_app_id) + str(batch_id) + str(timestamp)
        
        logger.debug("Đã tao chuỗi chuẩn hóa canonical string", 
                    source_app_id=source_app_id,
                    batch_id=batch_id, 
                    timestamp=timestamp,
                    canonical_string=canonical_string,
                    length=len(canonical_string))
        
        return canonical_string