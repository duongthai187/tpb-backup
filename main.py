import structlog
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
import time
from pathlib import Path

from app.models import WebhookRequest, WebhookResponse, TransactionResult
from app.middlewares.ip_whitelist import IPWhitelistMiddleware
from app.middlewares.rate_limit import RateLimitMiddleware
from app.middlewares.signature_verification import SignatureVerificationMiddleware
# from app.middlewares.bank_certificate import BankCertificateMiddleware  # Optional: for cert-based auth
from app.services.webhook_processor import WebhookProcessor
from app.services.metrics_collector import get_metrics_collector
from app.config.settings import settings

# Initialize structured logger
logger = structlog.get_logger()

REGISTRY._collector_to_names.clear()
REGISTRY._names_to_collectors.clear()
# Prometheus metrics
webhook_requests_total = Counter(
    'webhook_requests_total',
    'Total webhook requests',
    ['method', 'endpoint', 'status']
)

webhook_request_duration = Histogram(
    'webhook_request_duration_seconds',
    'Webhook request duration',
    ['method', 'endpoint']
)

signature_verification_total = Counter(
    'signature_verification_total',
    'Total signature verifications',
    ['status']
)

app = FastAPI(
    title="Bank Webhook Notify",
    description="Secure webhook endpoint for receiving bank notifications",
    version="1.0.0",
    docs_url="/docs" if settings.reload else None,  # Disable docs in production
    # redoc_url=None
)

# Add CORS middleware - mostly for dashboard access, not webhook security
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",   # Dashboard
        "http://127.0.0.1:8501"
    ],
    allow_credentials=False,      # Dashboard doesn't need credentials
    allow_methods=["GET", "POST"],  
    allow_headers=["*"],
)

# Add custom middlewares
app.add_middleware(RateLimitMiddleware)
app.add_middleware(IPWhitelistMiddleware)
app.add_middleware(SignatureVerificationMiddleware)

# Initialize webhook processor with database path
webhook_processor = WebhookProcessor(db_path="webhook_metrics.db")

# Initialize metrics collector
metrics_collector = get_metrics_collector()


# @app.middleware("http")
# async def add_proxy_headers(request: Request, call_next):
#     """Handle reverse proxy headers for production deployment"""
#     # Handle reverse proxy headers
#     forwarded_proto = request.headers.get("X-Forwarded-Proto")
#     if forwarded_proto:
#         request.scope["scheme"] = forwarded_proto
    
#     forwarded_host = request.headers.get("X-Forwarded-Host")
#     if forwarded_host:
#         port = 443 if forwarded_proto == "https" else 80
#         request.scope["server"] = (forwarded_host, port)
    
#     # Get real client IP from proxy headers
#     real_ip = (
#         request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or
#         request.headers.get("X-Real-IP") or
#         request.client.host if request.client else "unknown"
#     )
    
#     # Update client info for downstream middlewares
#     if request.client:
#         # Store original client info
#         request.scope["client"] = (real_ip, request.client.port)
    
#     return await call_next(request)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add processing time and logging middleware"""
    start_time = time.time()
    
    # Get real client IP (after proxy middleware processing)
    client_ip = request.client.host if request.client else "unknown"
    
    # Log incoming request
    logger.info(
        "Request received",
        method=request.method,
        url=str(request.url),
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        forwarded_proto=request.headers.get("X-Forwarded-Proto"),
        forwarded_host=request.headers.get("X-Forwarded-Host")
    )
    
    response = await call_next(request)
    
    # Calculate processing time
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    
    # Update metrics
    webhook_requests_total.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code
    ).inc()
    
    webhook_request_duration.labels(
        method=request.method,
        endpoint=request.url.path
    ).observe(process_time)
    
    # Log response
    logger.info(
        "request_processed",
        method=request.method,
        url=str(request.url),
        status_code=response.status_code,
        process_time=process_time,
        client_ip=client_ip
    )
    
    return response


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }


@app.get("/api/metrics/summary")
async def get_metrics_summary():
    try:
        summary = metrics_collector.get_summary_stats()
        return JSONResponse(content=summary)
    except Exception as e:
        logger.error("Failed to get metrics summary", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to get metrics summary", "detail": str(e)}
        )

@app.get("/admin/uat/summary")
async def get_uat_summary_stats():
    """Get UAT webhook summary statistics"""
    try:
        stats = metrics_collector.get_summary_stats(is_uat=True)
        return {
            "success": True,
            "data": stats,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error("Error getting UAT summary stats", error=str(e))
        return {
            "success": False,
            "error": "Failed to get UAT summary stats", 
            "detail": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/api/metrics/webhooks")
async def get_webhook_metrics(hours: int = 24, limit: int = 100):
    try:
        if hours <= 1:
            # For recent data, use in-memory cache
            metrics = metrics_collector.get_recent_webhooks(limit=limit)
        else:
            # For historical data, use database
            metrics = metrics_collector.get_webhook_metrics_from_db(hours=hours)[:limit]
        
        return JSONResponse(content={"metrics": metrics, "count": len(metrics)})
    except Exception as e:
        logger.error("Failed to get webhook metrics", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to get webhook metrics", "detail": str(e)}
        )


@app.get("/api/metrics/uat/webhooks")
async def get_uat_webhook_metrics(hours: int = 24, limit: int = 100):
    try:
        metrics = metrics_collector.get_webhook_metrics_from_db(hours=hours, is_uat=True)[:limit]
        
        return JSONResponse(content={"metrics": metrics, "count": len(metrics)})
    except Exception as e:
        logger.error("Failed to get UAT webhook metrics", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to get UAT webhook metrics", "detail": str(e)}
        )


@app.get("/api/metrics/hourly")
async def get_hourly_stats(hours: int = 24):
    try:
        stats = metrics_collector.get_hourly_stats(hours=hours)
        return JSONResponse(content={"hourly_stats": stats})
    except Exception as e:
        logger.error("Failed to get hourly stats", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to get hourly stats", "detail": str(e)}
        )


@app.get("/api/analysis/webhook-files")
async def get_webhook_file_analysis():
    try:
        analysis = metrics_collector.analyze_webhook_files()
        return JSONResponse(content={"analysis": analysis})
    except Exception as e:
        logger.error("Failed to analyze webhook files", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to analyze webhook files", "detail": str(e)}
        )


@app.post("/webhook/bank-notification", response_model=WebhookResponse)
async def receive_bank_notification(
    webhook_data: WebhookRequest,
    request: Request
):
    """
    Main webhook endpoint to receive bank notifications
    
    This endpoint:
    1. Receives POST requests with bank transaction data
    2. Validates digital signature (SHA512withRSA)
    3. Processes the transaction data
    4. Returns success/error response
    
    Security features applied via middleware:
    - IP whitelist validation
    - Rate limiting
    - Signature verification
    - mTLS (configured at server level)
    """
    try:
        start_time = datetime.now()
        
        logger.info(
            "Nhận webhook",
            batch_id=webhook_data.batch_id,
            source_app_id=webhook_data.source_app_id,
            transaction_count=len(webhook_data.data),
            timestamp=webhook_data.timestamp
        )
        
        # Process webhook data
        result = await webhook_processor.process_notification(webhook_data)
        
        # Create response data array với kết quả từng transaction
        response_data = []
        
        # Xử lý các transaction thành công
        for transaction in webhook_data.data:
            transaction_found = False
            
            # Kiểm tra nếu transaction bị failed
            for failed_tx in result.get("failed_transactions", []):
                if failed_tx["transaction_id"] == transaction.transaction_id:
                    # Determine error code based on error type
                    error_code = "01"  # Default: thất bại cần resend
                    
                    if "Giao dịch trùng lặp" in failed_tx["error"]:
                        error_code = "02"  # Thất bại không cần resend
                    elif "Validation failed" in failed_tx["error"]:
                        error_code = "01"  # Thất bại cần resend
                    
                    response_data.append({
                        "transactionId": transaction.transaction_id,
                        "errorCode": error_code,
                        "description": failed_tx["error"],
                    })
                    transaction_found = True
                    break
            
            # Nếu không có trong failed list, nghĩa là thành công
            if not transaction_found:
                response_data.append({
                    "transactionId": transaction.transaction_id,
                    "errorCode": "00",  # Thành công
                    "description": "Giao dịch thành công"
                })
        
        # Determine overall response code
        overall_success = result["success"]
        response_code = "200" if overall_success else "400"
        response_message = "Success" if overall_success else "Some transactions failed"
        
        logger.info(
            "Đã xử lý webhook",
            batch_id=webhook_data.batch_id,
            processed_count=result.get("processed_count", 0),
            failed_count=result.get("failed_count", 0),
            overall_success=overall_success
        )
        
        # Record metrics for dashboard
        process_time = (datetime.now() - start_time).total_seconds()
        metrics_collector.record_webhook_event(
            batch_id=webhook_data.batch_id,
            source_app_id=webhook_data.source_app_id,
            transaction_count=len(webhook_data.data),
            processed_count=result.get("processed_count", 0),
            failed_count=result.get("failed_count", 0),
            process_time=process_time,
            status_code=200 if overall_success else 400,
            client_ip=request.client.host if request.client else "unknown",
            error_message= 'Thành công' if overall_success else "Một số giao dịch thất bại",
        )
        
        return WebhookResponse(
            batch_id=webhook_data.batch_id,
            code=response_code,
            message=response_message,
            data=response_data
        )
            
    except Exception as e:
        logger.error(
            "Lỗi khi xử lý webhook",
            batch_id=getattr(webhook_data, 'batch_id', 'unknown'),
            error=str(e),
            exc_info=True
        )
        
        # Return error response in required format
        error_data = []
        if hasattr(webhook_data, 'data'):
            for transaction in webhook_data.data:
                error_data.append({
                    "transactionId": transaction.transaction_id,
                    "errorCode": "01",  # Thất bại cần resend
                    "description": "Internal server error"
                })
        
        return WebhookResponse(
            batch_id=getattr(webhook_data, 'batch_id', 'unknown'),
            code="500",
            message="Internal server error",
            data=error_data
        )

@app.post("/webhook/uat-notification", response_model=WebhookResponse)
async def receive_uat_notification(
    webhook_data: WebhookRequest,
    request: Request
):
    """
    UAT webhook endpoint for testing bank notifications
    
    Differences from production endpoint:
    1. Relaxed security (no IP whitelist, loose rate limiting)  
    2. Additional debug information in response
    3. Optional signature validation bypass for testing
    4. More detailed error messages
    
    Note: This endpoint is for UAT/testing only, not for production use
    """
    try:
        start_time = datetime.now()
        
        # Process webhook data using UAT method
        result = await webhook_processor.process_notification(webhook_data, is_uat=True)
        
        # Create response data array với debug info
        response_data = []
        debug_info = {
            "processing_time": (datetime.now() - start_time).total_seconds(),
            "endpoint": "UAT",
            "signature_validated": request.headers.get("X-Signature-Validated", "false"),
            "rate_limit_remaining": request.headers.get("X-RateLimit-Remaining", "unknown"),
            "client_info": {
                "ip": request.client.host if request.client else "unknown",
                "user_agent": request.headers.get("user-agent", ""),
                "forwarded_for": request.headers.get("X-Forwarded-For", "none")
            }
        }
        
        # Process successful and failed transactions
        for transaction in webhook_data.data:
            transaction_found = False

            # Check for failed transactions
            for failed_tx in result.get("failed_transactions", []):
                if failed_tx["transaction_id"] == transaction.transaction_id:
                    error_code = "01" # Default: thất bại cần resend
                    additional_info = {
                        "error_detail": failed_tx["error"],
                        "debug_info": debug_info
                    }
                    
                    if "Giao dịch trùng lặp" in failed_tx["error"]:
                        error_code = "02"  # Thất bại không cần resend
                        additional_info["error_detail"] = 'Giao dịch trùng lặp'
                    elif "Validation failed" in failed_tx["error"]:
                        error_code = "01"  # Thất bại cần resend
                        additional_info["error_detail"] = failed_tx["error"]
                    
                    response_data.append({
                        "transactionId": transaction.transaction_id,
                        "errorCode": error_code,
                        "description": failed_tx["error"],
                        "additionalInfo": additional_info
                    })
                    transaction_found = True
                    break
            
            # Add successful transactions
            if not transaction_found:
                response_data.append({
                    "transactionId": transaction.transaction_id,
                    "errorCode": "00",  # Thành công
                    "description": f"Xử lý {transaction.transaction_id} thành công",
                    "additionalInfo": {
                        "debug_info": debug_info
                    }
                })
        
        # Determine response
        process_time = (datetime.now() - start_time).total_seconds()
        total_transactions = len(webhook_data.data)
        failed_count = result.get("failed_count", 0)
        overall_success = failed_count == 0
        
        response_code = "200" if overall_success else "400"
        response_message = f"UAT: Processed {total_transactions - failed_count}/{total_transactions} transactions xử lý thành công"
        
        # Enhanced UAT logging
        logger.info(
            "Đã xử lý UAT webhook",
            batch_id=webhook_data.batch_id,
            total_transactions=total_transactions,
            processed_count=result.get("processed_count", 0),
            failed_count=failed_count,
            process_time=process_time,
            success_rate=((total_transactions - failed_count) / total_transactions) if total_transactions > 0 else 0,
            debug_info=debug_info
        )
        
        # Record UAT metrics in separate table
        metrics_collector.record_webhook_event(
            batch_id=webhook_data.batch_id,
            source_app_id=webhook_data.source_app_id,  # Keep original source_app_id
            transaction_count=len(webhook_data.data),
            processed_count=result.get("processed_count", 0),
            failed_count=result.get("failed_count", 0),
            process_time=process_time,
            status_code=200,
            client_ip=request.client.host if request.client else "unknown",
            error_message=None if overall_success else f"UAT: {failed_count} failures",
            is_uat=True
        )
        
        return WebhookResponse(
            batch_id=webhook_data.batch_id,
            code=response_code,
            message=response_message,
            data=response_data
        )
        
    except Exception as e:
        process_time = (datetime.now() - start_time).total_seconds()
        
        logger.error(
            "Lỗi khi xử lý UAT webhook",
            batch_id=getattr(webhook_data, 'batch_id', 'unknown'),
            error=str(e),
            process_time=process_time,
            exc_info=True
        )
        
        # Enhanced error response for UAT
        error_data = []
        if hasattr(webhook_data, 'data'):
            for transaction in webhook_data.data:
                error_data.append({
                    "transactionId": transaction.transaction_id,
                    "errorCode": "01", # Thất bại cần resend
                    "description": f"Lỗi khi xử lý UAT batch: {str(e)}"
                })
        
        # Record failed UAT metrics
        if hasattr(webhook_data, 'batch_id') and hasattr(webhook_data, 'source_app_id'):
            metrics_collector.record_webhook_event(
                batch_id=webhook_data.batch_id,
                source_app_id=webhook_data.source_app_id,
                transaction_count=len(getattr(webhook_data, 'data', [])),
                processed_count=0,
                failed_count=len(getattr(webhook_data, 'data', [])),
                process_time=process_time,
                status_code=500,
                client_ip=request.client.host if request.client else "unknown",
                error_message=str(e),
                is_uat=True
            )
        
        return WebhookResponse(
            batch_id=getattr(webhook_data, 'batch_id', 'unknown'),
            code="500",
            message=f"UAT: Internal server error - {str(e)}",
            data=error_data
        )


@app.get("/admin/processed-transactions/stats")
async def get_processed_transactions_stats():
    try:
        stats = webhook_processor.get_processed_transactions_stats()
        print(stats)
        return {
            "success": True,
            "data": stats,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error("Error getting processed transactions stats", error=str(e))
        return {
            "success": False,
            "error": "Failed to get processed transactions stats",
            "detail": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.get("/admin/uat/files")
async def get_uat_files(date: str = None):
    """List UAT webhook files"""
    try:
        
        uat_storage_dir = Path("webhook_notifications_uat")
        if not uat_storage_dir.exists():
            return {
                "success": True,
                "message": "No UAT files found - directory doesn't exist",
                "files": [],
                "count": 0
            }
        
        files_info = []
        
        if date:
            # List files for specific date
            date_folder = uat_storage_dir / date
            if date_folder.exists():
                for file_path in sorted(date_folder.glob("UAT_*.json")):
                    file_stat = file_path.stat()
                    files_info.append({
                        "filename": file_path.name,
                        "path": str(file_path),
                        "size": file_stat.st_size,
                        "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                        "date": date
                    })
        else:
            # List all dates and recent files
            for date_folder in sorted(uat_storage_dir.iterdir()):
                if date_folder.is_dir():
                    for file_path in sorted(date_folder.glob("UAT_*.json"))[-5:]:  # Last 5 files per date
                        file_stat = file_path.stat()
                        files_info.append({
                            "filename": file_path.name,
                            "path": str(file_path),
                            "size": file_stat.st_size,
                            "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                            "date": date_folder.name
                        })
        
        return {
            "success": True,
            "files": files_info,
            "count": len(files_info),
            "storage_path": str(uat_storage_dir),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Error listing UAT files", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/admin/uat/file/{filename}")
async def get_uat_file_content(filename: str, date: str = None):
    """Get content of specific UAT file"""
    try:
        uat_storage_dir = Path("webhook_notifications_uat")
        
        # Try to find the file
        file_path = None
        
        if date:
            # Look in specific date folder
            file_path = uat_storage_dir / date / filename
        else:
            # Search in all date folders
            for date_folder in uat_storage_dir.iterdir():
                if date_folder.is_dir():
                    potential_path = date_folder / filename
                    if potential_path.exists():
                        file_path = potential_path
                        break
        
        if not file_path or not file_path.exists():
            raise HTTPException(status_code=404, detail=f"UAT file '{filename}' not found")
        
        # Read and return file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        
        file_stat = file_path.stat()
        
        return {
            "success": True,
            "filename": filename,
            "path": str(file_path),
            "size": file_stat.st_size,
            "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error reading UAT file", filename=filename, error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    logger.error(
        "http_exception",
        status_code=exc.status_code,
        detail=exc.detail,
        url=str(request.url),
        method=request.method
    )
    
    # For webhook endpoints, return in required format
    if request.url.path.startswith("/webhook/"):
        # Try to get batch_id from request if possible
        batch_id = "unknown"
        try:
            if request.method == "POST":
                # This is a simplified approach - in real scenario you might need to parse body
                batch_id = "error_batch"
        except:
            pass
            
        return JSONResponse(
            status_code=200,  # Always return 200 for webhook responses as per bank requirement
            content={
                "batchId": batch_id,
                "code": str(exc.status_code),
                "message": exc.detail,
                "data": []
            }
        )
    
    # For other endpoints, return standard format
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.detail,
            "timestamp": datetime.now().isoformat()
        }
    )