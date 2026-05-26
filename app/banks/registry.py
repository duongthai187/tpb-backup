"""
BankRegistry — central lookup for all registered bank handlers.

Usage:
    from app.banks.registry import bank_registry

    # Register at startup (in main.py lifespan)
    bank_registry.register(TPBankHandler())

    # Look up by bank_id from URL path
    handler = bank_registry.get("tpbank")   # raises KeyError if not found
"""
from __future__ import annotations

from typing import Dict, Iterator

import structlog

from app.banks.base import BankHandler

logger = structlog.get_logger()


class BankRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, BankHandler] = {}

    def register(self, handler: BankHandler) -> None:
        """Register a BankHandler.  Overwrites if same bank_id registered twice."""
        self._handlers[handler.bank_id] = handler
        logger.info("BankRegistry: handler registered", bank_id=handler.bank_id)

    def get(self, bank_id: str) -> BankHandler:
        """
        Return the handler for bank_id.
        Raises KeyError with a descriptive message if not registered.
        """
        if bank_id not in self._handlers:
            available = list(self._handlers.keys())
            raise KeyError(
                f"No handler registered for bank_id='{bank_id}'. "
                f"Available: {available}"
            )
        return self._handlers[bank_id]

    def is_registered(self, bank_id: str) -> bool:
        return bank_id in self._handlers

    def all_bank_ids(self) -> list[str]:
        return list(self._handlers.keys())

    def __iter__(self) -> Iterator[BankHandler]:
        return iter(self._handlers.values())

    def __len__(self) -> int:
        return len(self._handlers)


# Module-level singleton — import this everywhere
bank_registry = BankRegistry()
