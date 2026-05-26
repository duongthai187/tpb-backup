"""
Abstract interfaces that every bank integration MUST implement.

To add a new bank (e.g. BIDV):
  1. Create  app/banks/bidv/__init__.py
  2. Subclass BankHandler, PayloadParser, SignatureVerifier, TransactionValidator
  3. Call    bank_registry.register(BidvHandler())  at startup

Nothing else in the codebase needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List


# ── Per-bank configuration snapshot ──────────────────────────────────────────

@dataclass
class BankConfig:
    """Runtime config for a single bank integration."""

    bank_id: str                        # slug, e.g. "tpbank"
    display_name: str                   # e.g. "TPBank"
    public_key_file: str = ""           # path to PEM public key


# ── Strategy interfaces ───────────────────────────────────────────────────────

class SignatureVerifier(ABC):
    """Verifies the authenticity of an incoming webhook request."""

    @abstractmethod
    def verify(self, payload: Dict[str, Any], signature: str) -> bool:
        """
        Return True if the signature over payload is valid.
        Raise nothing — callers check the return value.
        """


class PayloadParser(ABC):
    """Transforms a raw JSON dict into a NormalizedBatch."""

    @abstractmethod
    def parse(self, raw: Dict[str, Any], bank_id: str, is_uat: bool = False):
        """
        Return a NormalizedBatch.
        Raise ValueError if required fields are missing.
        """


class TransactionValidator(ABC):
    """Applies bank-specific business rules to a NormalizedTransaction."""

    @abstractmethod
    def validate(self, tx) -> List[str]:
        """
        Return a (possibly empty) list of human-readable error strings.
        Empty list means the transaction is valid.
        """


# ── Top-level handler (aggregates the three strategies) ──────────────────────

class BankHandler(ABC):
    """
    One BankHandler per bank.  Aggregates:
      - config           (BankConfig)
      - verifier         (SignatureVerifier)
      - parser           (PayloadParser)
      - validator        (TransactionValidator)

    The bank_id property is the registry key.
    """

    @property
    @abstractmethod
    def bank_id(self) -> str:
        """Unique slug identifying this bank, e.g. 'tpbank'."""

    @property
    @abstractmethod
    def config(self) -> BankConfig:
        """Return the BankConfig for this integration."""

    @property
    @abstractmethod
    def verifier(self) -> SignatureVerifier:
        """Return the SignatureVerifier for this bank."""

    @property
    @abstractmethod
    def parser(self) -> PayloadParser:
        """Return the PayloadParser for this bank."""

    @property
    @abstractmethod
    def validator(self) -> TransactionValidator:
        """Return the TransactionValidator for this bank."""

    def extract_batch_id(self, raw: Dict[str, Any]) -> str:
        """
        Optional hook: extract batch_id from raw payload for error responses.
        Default implementation looks for common field names.
        Override if the bank uses a different field.
        """
        for key in ("batchId", "batch_id", "BatchId", "BATCHID"):
            if key in raw:
                return str(raw[key])
        return "unknown"
