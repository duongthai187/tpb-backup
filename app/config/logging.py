import logging
import sys
from typing import Any, Dict

import structlog

from app.config.settings import settings


def _add_service_context(_, __, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    event_dict["service"] = "webhook-api"
    event_dict["version"] = "2.0.0"
    return event_dict


def setup_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_service_context,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
