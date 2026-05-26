from __future__ import annotations

from typing import Any, Dict

import structlog

from app.banks.base import BankConfig, BankHandler, PayloadParser, SignatureVerifier, TransactionValidator
from app.banks.tpbank.parser import TPBankParser
from app.banks.tpbank.settings import tpbank_settings
from app.banks.tpbank.validator import TPBankValidator
from app.banks.tpbank.verifier import TPBankVerifier

logger = structlog.get_logger()

_BANK_ID = "tpbank"


class TPBankHandler(BankHandler):
    def __init__(self) -> None:
        self._config = BankConfig(
            bank_id=_BANK_ID,
            display_name="TPBank",
            public_key_file=tpbank_settings.public_key_file,
        )
        self._verifier = TPBankVerifier()
        self._parser = TPBankParser()
        self._validator = TPBankValidator()
        logger.info("tpbank.handler.initialized", bank_id=_BANK_ID)

    @property
    def bank_id(self) -> str:
        return _BANK_ID

    @property
    def config(self) -> BankConfig:
        return self._config

    @property
    def verifier(self) -> SignatureVerifier:
        return self._verifier

    @property
    def parser(self) -> PayloadParser:
        return self._parser

    @property
    def validator(self) -> TransactionValidator:
        return self._validator

    def extract_batch_id(self, raw: Dict[str, Any]) -> str:
        return str(raw["batchId"]) if "batchId" in raw else "unknown"


tpbank_handler = TPBankHandler()
