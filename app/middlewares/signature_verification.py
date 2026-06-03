"""
Signature Verification Middleware
Intercepts POST /webhook/{bank_id}/... requests, validates the payload
signature via the registered bank handler's verifier, then re-injects the
body so downstream route handlers can read it normally.
"""
import json
import re

import structlog
from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.banks.registry import bank_registry

logger = structlog.get_logger(__name__)

signature_verification_total = Counter(
    "signature_verification_total",
    "Total signature verification attempts",
    ["bank_id", "status"],  # status = success | invalid | missing | error
)

_WEBHOOK_RE = re.compile(r"^/webhook/([^/]+)")


def _reject(batch_id: str, code: str, message: str) -> JSONResponse:
    """Return a 200 response that carries an application-level error."""
    return JSONResponse(
        status_code=200,
        content={
            "batchId": batch_id,
            "code": code,
            "message": message,
            "data": [],
        },
    )


class SignatureVerificationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Only intercept POST /webhook/... requests
        if not (method == "POST" and path.startswith("/webhook/")):
            return await call_next(request)

        # Extract bank_id
        match = _WEBHOOK_RE.match(path)
        if not match:
            return await call_next(request)

        bank_id = match.group(1)

        # Resolve bank handler
        try:
            handler = bank_registry.get(bank_id)
        except KeyError:
            logger.warning("sig_verify.unknown_bank", bank_id=bank_id, path=path)
            return _reject("unknown", "404", f"Unknown bank: {bank_id}")

        # Read and parse body
        try:
            raw_body = await request.body()
            payload: dict = json.loads(raw_body)
        except (json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
            logger.warning("sig_verify.bad_body", bank_id=bank_id, error=str(exc))
            return _reject("unknown", "400", "Invalid JSON body")

        batch_id: str = str(payload.get("batchId", "unknown"))

        # Extract signature
        signature = payload.get("signature")
        if not signature:
            logger.warning("sig_verify.missing_signature", bank_id=bank_id, batch_id=batch_id)
            signature_verification_total.labels(bank_id=bank_id, status="missing").inc()
            return _reject(batch_id, "401", "Missing signature")

        # Build payload without the signature field for verification
        payload_without_signature = {k: v for k, v in payload.items() if k != "signature"}

        # Verify
        is_uat = path.endswith("/uat")
        try:
            valid = handler.verifier.verify(payload_without_signature, signature, is_uat=is_uat)
        except Exception as exc:  # noqa: BLE001
            logger.error("sig_verify.verifier_error", bank_id=bank_id, batch_id=batch_id, error=str(exc))
            signature_verification_total.labels(bank_id=bank_id, status="error").inc()
            return _reject(batch_id, "500", "Signature verification error")

        if not valid:
            logger.warning("sig_verify.invalid_signature", bank_id=bank_id, batch_id=batch_id)
            signature_verification_total.labels(bank_id=bank_id, status="invalid").inc()
            return _reject(batch_id, "401", "Invalid signature")

        logger.debug("sig_verify.ok", bank_id=bank_id, batch_id=batch_id)
        signature_verification_total.labels(bank_id=bank_id, status="success").inc()

        # Re-inject the original body so the route handler can read it
        async def _receive():
            return {"type": "http.request", "body": raw_body, "more_body": False}

        request._receive = _receive  # noqa: SLF001

        return await call_next(request)
