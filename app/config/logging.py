import sys
import logging
import structlog
from pythonjsonlogger import jsonlogger
from typing import Any, Dict

from app.config.settings import settings

def setup_logging():    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )
    
    # Create JSON formatter for structured logs
    json_handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    json_handler.setFormatter(formatter)
    
    # Configure structlog
    structlog.configure(
        processors=[
            # Add log level to event dict
            structlog.stdlib.add_log_level,
            # Add logger name to event dict
            structlog.stdlib.add_logger_name,
            # Add timestamp to event dict
            structlog.processors.TimeStamper(fmt="iso"),
            # Add hostname/service info
            add_service_context,
            # Format as JSON for production
            structlog.processors.JSONRenderer() if settings.log_level != "DEBUG" 
            else structlog.dev.ConsoleRenderer(colors=True)
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

def add_service_context(_, __, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Add service context to all log entries"""
    event_dict.update({
        "service": "webhook-api",
        "version": "1.0.0",
        "environment": "production" if not settings.reload else "development"
    })
    return event_dict

def get_logger(name: str = None):
    """Get configured logger instance"""
    return structlog.get_logger(name) if name else structlog.get_logger()

setup_logging()