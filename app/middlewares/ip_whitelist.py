import ipaddress
from typing import List, Union
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import structlog

from app.config.settings import settings

logger = structlog.get_logger()


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    
    def __init__(self, app):
        super().__init__(app)
        self.allowed_networks = self._parse_allowed_ips(settings.allowed_ips)
        logger.info("Định nghĩa danh sách IP Whitelist thành công", allowed_networks=len(self.allowed_networks))
    
    def _parse_allowed_ips(self, allowed_ips: List[str]) -> List[Union[ipaddress.IPv4Network, ipaddress.IPv6Network]]:
        networks = []
        
        for ip_str in allowed_ips:
            try:
                # Try to parse as network (supports both single IPs and CIDR)
                network = ipaddress.ip_network(ip_str, strict=False)
                networks.append(network)
                logger.info("Đã thêm mạng mới: ", network=str(network))
            except ValueError as e:
                logger.error("Lỗi không hợp lệ (_parse_allowed_ips)", ip=ip_str, error=str(e))
                continue
        
        return networks
    
    def _get_client_ip(self, request: Request) -> str:
        # Debug: Log all headers to see what we receive
        logger.info(
            "DEBUG Headers received",
            x_forwarded_for=request.headers.get("X-Forwarded-For"),
            x_real_ip=request.headers.get("X-Real-IP"),
            forwarded=request.headers.get("Forwarded"),
            x_forwarded_proto=request.headers.get("X-Forwarded-Proto"),
            x_forwarded_host=request.headers.get("X-Forwarded-Host"),
            direct_client=request.client.host if request.client else "unknown",
            all_headers=dict(request.headers)
        )
        
        # Check common proxy headers in order of preference
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For can contain multiple IPs, take the first one (original client)
            original_ip = forwarded_for.split(',')[0].strip()
            logger.info("Found X-Forwarded-For", original_ip=original_ip, full_header=forwarded_for)
            return original_ip
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            logger.info("Found X-Real-IP", real_ip=real_ip.strip())
            return real_ip.strip()
        
        forwarded = request.headers.get("Forwarded")
        if forwarded:
            # Parse Forwarded header (RFC 7239)
            # Format: for=192.0.2.60;proto=http;by=203.0.113.43
            for part in forwarded.split(';'):
                if part.strip().startswith('for='):
                    forwarded_ip = part.strip().split('=')[1].strip('"')
                    logger.info("Found Forwarded header", forwarded_ip=forwarded_ip)
                    return forwarded_ip
        
        # Fall back to direct connection IP
        direct_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "No proxy headers found, using direct IP", 
            direct_ip=direct_ip,
            note="This might be local IP if behind NAT/proxy without proper headers"
        )
        return direct_ip
    
    def _is_ip_allowed(self, client_ip: str) -> bool:
        # try:
        #     client_ip_obj = ipaddress.ip_address(client_ip)
            
        #     for network in self.allowed_networks:
        #         if client_ip_obj in network:
        #             return True
            
        #     return False
        # except ValueError as e:
        #     logger.error("Lỗi không hợp lệ (_is_ip_allowed)", client_ip=client_ip, error=str(e))
        #     return False
        return True
    
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/health", "/metrics"]:
            return await call_next(request)

        try:
            # Get client IP
            client_ip = self._get_client_ip(request)
            
            # Log the request attempt
            logger.info(
                "Kiểm tra IP Whitelist (dispatch)",
                client_ip=client_ip,
                path=request.url.path,
                method=request.method,
                user_agent=request.headers.get("user-agent", "")
            )
            
            # Check if IP is allowed
            if not self._is_ip_allowed(client_ip):
                logger.warning(
                    "Lỗi không hợp lệ IP (dispatch)",
                    client_ip=client_ip,
                    path=request.url.path,
                    method=request.method
                )
                
                return JSONResponse(
                    status_code=200,
                    content={
                        "batchId": "unknown",
                        "code": "403",
                        "message": "IP không được phép truy cập (dispatch)",
                        "data": []
                    }
                )
            
            logger.info("Pass kiểm tra IP Whitelist (dispatch)", client_ip=client_ip)
            
            # IP is allowed, proceed to next middleware
            return await call_next(request)
            
        except Exception as e:
            logger.error("ip_whitelist_error (dispatch)", error=str(e), exc_info=True)
            return JSONResponse(
                status_code=200,
                content={
                    "batchId": "unknown", 
                    "code": "500",
                    "message": "IP whitelist check error (dispatch)",
                    "data": []
                }
            )