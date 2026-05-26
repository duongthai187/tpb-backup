"""
Entry point — wires up Database, MessageProcessor, StreamConsumer,
registers graceful shutdown on SIGINT/SIGTERM, then starts the consumer loop.
"""
from __future__ import annotations

import logging
import signal
import sys

import structlog

from .config import settings
from .consumer import StreamConsumer
from .db import Database
from .processor import MessageProcessor


def _setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def main() -> int:
    _setup_logging()

    # Bind worker identity to every log line from this process
    structlog.contextvars.bind_contextvars(worker=settings.consumer_name)

    logger = structlog.get_logger()
    logger.info(
        "webhook-worker starting",
        stream=settings.stream_name,
        group=settings.consumer_group,
        consumer=settings.consumer_name,
        db_schema=settings.db_schema,
        db_table=settings.db_table,
    )

    database = Database()
    processor = MessageProcessor(database, worker_name=settings.consumer_name)
    consumer = StreamConsumer(processor)

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal, shutting down", signal=signum)
        consumer.stop()
        database.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    consumer.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
