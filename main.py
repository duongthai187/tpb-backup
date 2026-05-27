import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import redis
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.banks.registry import bank_registry
from app.banks.tpbank.handler import tpbank_handler
from app.config.logging import setup_logging
from app.config.settings import settings
from app.core.models import BatchProcessingResult
from app.core.processor import core_processor
from app.middlewares.signature_verification import SignatureVerificationMiddleware

logger = structlog.get_logger()

# ── Prometheus metrics ────────────────────────────────────────────────────────
webhook_requests_total = Counter(
    "webhook_requests_total",
    "Total webhook requests received",
    ["bank_id", "endpoint", "status"],
)
webhook_request_duration = Histogram(
    "webhook_request_duration_seconds",
    "Webhook request processing duration",
    ["bank_id", "endpoint"],
)
webhook_saved_files = Gauge(
    "webhook_saved_json_files_total",
    "Total number of saved webhook JSON notification files",
    ["env"],  # env = prod | uat
)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()

    bank_registry.register(tpbank_handler)
    logger.info("Banks registered", banks=bank_registry.all_bank_ids())

    Path("webhook_notifications").mkdir(parents=True, exist_ok=True)
    Path("webhook_notifications_uat").mkdir(parents=True, exist_ok=True)

    yield
    # Shutdown (add cleanup here if needed)


# ── Application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Bank Webhook Hub",
    version="2.0.0",
    docs_url="/docs" if settings.reload else None,
    lifespan=lifespan,
)

app.add_middleware(SignatureVerificationMiddleware)


# ── Request logging + metrics middleware ──────────────────────────────────────
@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.time()
    client_ip = request.client.host if request.client else "unknown"

    # Extract bank_id from /webhook/{bank_id}/...
    parts = request.url.path.split("/")
    bank_id = parts[2] if len(parts) >= 3 and parts[1] == "webhook" else "unknown"
    endpoint = request.url.path

    logger.info(
        "request_in",
        method=request.method,
        path=endpoint,
        bank_id=bank_id,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    response = await call_next(request)

    duration = time.time() - start
    response.headers["X-Process-Time"] = f"{duration:.4f}"

    webhook_requests_total.labels(
        bank_id=bank_id,
        endpoint=endpoint,
        status=response.status_code,
    ).inc()
    webhook_request_duration.labels(
        bank_id=bank_id,
        endpoint=endpoint,
    ).observe(duration)

    logger.info(
        "request_out",
        method=request.method,
        path=endpoint,
        bank_id=bank_id,
        status=response.status_code,
        duration_s=round(duration, 4),
        client_ip=client_ip,
    )
    return response


# ── Health + metrics ──────────────────────────────────────────────────────────
@app.get("/health")
async def health_check():
    now = datetime.now().isoformat()
    checks: Dict[str, Any] = {}
    healthy = True

    # Check Redis db=0 (dedup)
    try:
        r0 = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r0.ping()
        checks["redis_dedup"] = "ok"
    except Exception as exc:
        checks["redis_dedup"] = f"error: {exc}"
        healthy = False

    # Check Redis db=1 (stream)
    try:
        r1 = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_stream_db,
            password=settings.redis_password or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r1.ping()
        checks["redis_stream"] = "ok"
    except Exception as exc:
        checks["redis_stream"] = f"error: {exc}"
        healthy = False

    body = {
        "status": "healthy" if healthy else "unhealthy",
        "timestamp": now,
        "version": "2.0.0",
        "banks": bank_registry.all_bank_ids(),
        "checks": checks,
    }
    return JSONResponse(content=body, status_code=200 if healthy else 503)


@app.get("/metrics")
async def prometheus_metrics():
    # Đếm file JSON trong thư mục notifications — cập nhật Gauge mỗi lần scrape
    prod_count = len(list(Path("webhook_notifications").rglob("*.json")))
    uat_count  = len(list(Path("webhook_notifications_uat").rglob("*.json")))
    webhook_saved_files.labels(env="prod").set(prod_count)
    webhook_saved_files.labels(env="uat").set(uat_count)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Webhook endpoints ─────────────────────────────────────────────────────────
@app.post("/webhook/{bank_id}/notification")
async def receive_notification(bank_id: str, request: Request):
    """Production endpoint — receive and process bank transaction notifications."""
    return await _handle_webhook(bank_id, request, is_uat=False)


@app.post("/webhook/{bank_id}/uat")
async def receive_uat_notification(bank_id: str, request: Request):
    """UAT endpoint — same pipeline, adds debug_info to response."""
    return await _handle_webhook(bank_id, request, is_uat=True)


async def _handle_webhook(bank_id: str, request: Request, *, is_uat: bool) -> JSONResponse:
    raw_body = await request.body()
    try:
        raw: Dict[str, Any] = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("invalid_json", bank_id=bank_id, is_uat=is_uat, error=str(exc))
        return JSONResponse(content={"batchId": "unknown", "code": "400", "message": "Invalid JSON body", "data": []})

    try:
        handler = bank_registry.get(bank_id)
    except KeyError:
        logger.warning("unknown_bank", bank_id=bank_id)
        return JSONResponse(content={"batchId": "unknown", "code": "404", "message": f"Unknown bank: {bank_id}", "data": []})

    batch_id = handler.extract_batch_id(raw)

    try:
        batch = handler.parser.parse(raw, bank_id, is_uat=is_uat)
    except ValueError as exc:
        logger.warning("parse_error", bank_id=bank_id, batch_id=batch_id, error=str(exc))
        return JSONResponse(content={"batchId": batch_id, "code": "400", "message": f"Parse error: {exc}", "data": []})

    start_ts = datetime.now()
    try:
        result = await core_processor.process(batch, handler)
    except Exception as exc:
        logger.error("processing_error", bank_id=bank_id, batch_id=batch_id, is_uat=is_uat, error=str(exc), exc_info=True)
        return JSONResponse(content={"batchId": batch_id, "code": "500", "message": "Internal server error", "data": []})

    logger.info(
        "webhook_processed",
        bank_id=bank_id,
        batch_id=batch_id,
        is_uat=is_uat,
        processed=result.processed_count,
        failed=result.failed_count,
    )

    debug_info: Optional[Dict[str, Any]] = None
    if is_uat:
        debug_info = {
            "processing_time_s": (datetime.now() - start_ts).total_seconds(),
            "client_ip": request.client.host if request.client else "unknown",
            "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            "user_agent": request.headers.get("user-agent"),
        }

    return JSONResponse(content=_build_response(batch_id, result, debug_info=debug_info))


# ── UAT file browser ──────────────────────────────────────────────────────────
@app.get("/admin/uat/files")
async def list_uat_files(date: Optional[str] = None):
    """List saved UAT webhook JSON files."""
    try:
        uat_dir = Path("webhook_notifications_uat").resolve()
        if not uat_dir.exists():
            return {"success": True, "files": [], "count": 0}

        if date:
            target = (uat_dir / date).resolve()
            if not str(target).startswith(str(uat_dir)):
                raise HTTPException(status_code=400, detail="Invalid date parameter")
            folders = [target]
        else:
            folders = sorted(uat_dir.iterdir())

        files_info = []
        for folder in folders:
            if not (folder.is_dir() and folder.exists()):
                continue
            for fp in sorted(folder.glob("UAT_*.json"))[-10:]:
                stat = fp.stat()
                files_info.append({
                    "filename": fp.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "date": folder.name,
                })

        return {"success": True, "files": files_info, "count": len(files_info)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("list_uat_files_error", error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/admin/uat/file/{filename}")
async def get_uat_file(filename: str, date: Optional[str] = None):
    """Read a specific UAT webhook file."""
    try:
        uat_dir = Path("webhook_notifications_uat").resolve()
        file_path: Optional[Path] = None

        if date:
            candidate = (uat_dir / date / filename).resolve()
            if str(candidate).startswith(str(uat_dir)) and candidate.exists():
                file_path = candidate
        else:
            for folder in sorted(uat_dir.iterdir()):
                if folder.is_dir():
                    candidate = (folder / filename).resolve()
                    if str(candidate).startswith(str(uat_dir)) and candidate.exists():
                        file_path = candidate
                        break

        if not file_path:
            raise HTTPException(status_code=404, detail=f"File '{filename}' not found")

        content = json.loads(file_path.read_text(encoding="utf-8"))
        stat = file_path.stat()
        return {
            "success": True,
            "filename": filename,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "content": content,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_uat_file_error", filename=filename, error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error")


# ── Exception handler ─────────────────────────────────────────────────────────
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(
        "http_exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=str(request.url),
    )
    if request.url.path.startswith("/webhook/"):
        return JSONResponse(
            status_code=200,
            content={
                "batchId": "unknown",
                "code": str(exc.status_code),
                "message": exc.detail,
                "data": [],
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.detail,
            "timestamp": datetime.now().isoformat(),
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _build_response_data(
    result: BatchProcessingResult,
    debug_info: Optional[Dict[str, Any]] = None,
) -> list:
    data = []
    for tx_result in result.transaction_results:
        item: Dict[str, Any] = {
            "transactionId": tx_result.transaction_id,
            "errorCode": tx_result.error_code,
            "description": tx_result.description,
        }
        if debug_info is not None:
            additional = dict(tx_result.additional_info or {})
            additional.update(debug_info)
            item["additionalInfo"] = additional
        data.append(item)
    return data


def _build_response(
    batch_id: str,
    result: BatchProcessingResult,
    debug_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "batchId": batch_id,
        "code": "200" if result.success else "400",
        "message": "Success" if result.success else "Some transactions failed",
        "data": _build_response_data(result, debug_info=debug_info),
    }
