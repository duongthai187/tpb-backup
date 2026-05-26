"""
Core processing pipeline — bank-agnostic.

Responsibilities:
  1. Deduplication  (via DedupService)
  2. Validation     (delegated to BankHandler.validator)
  3. File persistence
  4. Result aggregation

The caller (route handler) is responsible for:
  - Parsing the raw payload → NormalizedBatch  (via BankHandler.parser)
  - Passing the NormalizedBatch here
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from app.core.dedup import dedup_service
from app.core.stream import stream_publisher
from app.core.models import (
    BatchProcessingResult,
    NormalizedBatch,
    NormalizedTransaction,
    TransactionProcessingResult,
)

if TYPE_CHECKING:
    from app.banks.base import BankHandler

logger = structlog.get_logger()

_STORAGE_ROOT = Path("webhook_notifications")
_STORAGE_UAT_ROOT = Path("webhook_notifications_uat")


class CoreProcessor:
    """
    Stateless processor — instantiate once and reuse.
    Requires a BankHandler to perform bank-specific validation.
    """

    async def process(
        self,
        batch: NormalizedBatch,
        handler: "BankHandler",
    ) -> BatchProcessingResult:
        payload = await self._persist(batch)
        stream_publisher.publish(payload)

        results: list[TransactionProcessingResult] = []
        processed = 0

        logger.info(
            "Processing batch",
            bank_id=batch.bank_id,
            batch_id=batch.batch_id,
            tx_count=len(batch.transactions),
            is_uat=batch.is_uat,
        )

        for tx in batch.transactions:
            result = await self._process_transaction(tx, batch.is_uat, handler)
            results.append(result)
            if result.success:
                processed += 1

        failed = len(batch.transactions) - processed
        logger.info(
            "Batch complete",
            bank_id=batch.bank_id,
            batch_id=batch.batch_id,
            processed=processed,
            failed=failed,
        )

        return BatchProcessingResult(
            batch_id=batch.batch_id,
            bank_id=batch.bank_id,
            total=len(batch.transactions),
            processed_count=processed,
            failed_count=failed,
            success=(failed == 0),
            transaction_results=results,
        )

    async def _process_transaction(
        self,
        tx: NormalizedTransaction,
        is_uat: bool,
        handler: "BankHandler",
    ) -> TransactionProcessingResult:
        try:
            # 1. Dedup
            if dedup_service.is_duplicate(tx.bank_id, tx.transaction_id, is_uat):
                logger.warning("Duplicate transaction", txn_id=tx.transaction_id, bank_id=tx.bank_id)
                return TransactionProcessingResult(
                    transaction_id=tx.transaction_id,
                    success=False,
                    error_code="02",
                    description="Giao dịch trùng lặp",
                )

            # 2. Validate (bank-specific rules)
            errors = handler.validator.validate(tx)
            if errors:
                logger.error("Validation failed", txn_id=tx.transaction_id, errors=errors)
                return TransactionProcessingResult(
                    transaction_id=tx.transaction_id,
                    success=False,
                    error_code="01",
                    description=f"Validation failed: {', '.join(errors)}",
                )

            # 3. Mark processed
            dedup_service.mark_processed(tx.bank_id, tx.transaction_id, is_uat)

            return TransactionProcessingResult(
                transaction_id=tx.transaction_id,
                success=True,
                error_code="00",
                description="Giao dịch thành công",
            )

        except Exception as exc:
            logger.error("Transaction processing exception", txn_id=tx.transaction_id, error=str(exc), exc_info=True)
            return TransactionProcessingResult(
                transaction_id=tx.transaction_id,
                success=False,
                error_code="01",
                description=f"Processing exception: {exc}",
            )

    async def _persist(self, batch: NormalizedBatch) -> dict:
        """Persist batch to JSON file and return the payload dict for stream publishing."""
        payload = {
            "received_at": batch.received_at.isoformat(),
            "bank_id": batch.bank_id,
            "batch_id": batch.batch_id,
            "source_app_id": batch.source_app_id,
            "timestamp": batch.timestamp,
            "is_uat": batch.is_uat,
            "transaction_count": len(batch.transactions),
            "transactions": [_tx_to_dict(tx) for tx in batch.transactions],
        }
        try:
            root = _STORAGE_UAT_ROOT if batch.is_uat else _STORAGE_ROOT
            ts = batch.received_at
            folder = root / ts.strftime("%Y%m%d")
            folder.mkdir(parents=True, exist_ok=True)

            prefix = "UAT_" if batch.is_uat else ""
            filename = f"{prefix}{ts.strftime('%Y%m%d_%H%M%S_%f')}_{batch.bank_id}_{batch.batch_id}.json"

            (folder / filename).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Batch persisted", file=str(folder / filename), bank_id=batch.bank_id)
        except Exception as exc:
            logger.error("Failed to persist batch", batch_id=batch.batch_id, error=str(exc))
        return payload


def _tx_to_dict(tx: NormalizedTransaction) -> dict:
    return {
        "transaction_id": tx.transaction_id,
        "bank_id": tx.bank_id,
        "batch_id": tx.batch_id,
        "amount": tx.amount,
        "trans_type": tx.trans_type,
        "src_account_number": tx.src_account_number,
        "tran_refno": tx.tran_refno,
        "balance_available": tx.balance_available,
        "notice_date_time": tx.notice_date_time,
        "trans_time": tx.trans_time,
        "trans_desc": tx.trans_desc,
        "ofs_account_number": tx.ofs_account_number,
        "ofs_account_name": tx.ofs_account_name,
        "ofs_bank_id": tx.ofs_bank_id,
        "ofs_bank_name": tx.ofs_bank_name,
        "is_virtual_trans": tx.is_virtual_trans,
        "virtual_acc": tx.virtual_acc,
        "received_at": tx.received_at.isoformat(),
    }


# Singleton
core_processor = CoreProcessor()
