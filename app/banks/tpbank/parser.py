from __future__ import annotations

from typing import Any, Dict

import structlog
from pydantic import ValidationError

from app.banks.base import PayloadParser
from app.banks.tpbank.models import WebhookRequest
from app.core.models import NormalizedBatch, NormalizedTransaction

logger = structlog.get_logger()


class TPBankParser(PayloadParser):
    def parse(self, raw: Dict[str, Any], bank_id: str, is_uat: bool = False) -> NormalizedBatch:
        try:
            request = WebhookRequest.model_validate(raw)
        except ValidationError as exc:
            logger.error("tpbank.parser.validation_error", errors=exc.errors())
            raise ValueError(f"TPBank payload validation failed: {exc}") from exc

        raw_transactions: list[dict] = raw.get("data", [])
        transactions = [
            NormalizedTransaction(
                transaction_id=tx.transaction_id,
                bank_id=bank_id,
                batch_id=request.batch_id,
                amount=tx.amount,
                trans_type=tx.trans_type,
                src_account_number=tx.account_number,
                tran_refno=tx.tran_refno,
                balance_available=tx.balance_available,
                notice_date_time=tx.noti_created_time,
                trans_time=tx.trans_time,
                trans_desc=tx.tran_desc,
                ofs_account_number=tx.ofs_account_number,
                ofs_account_name=tx.ofs_account_name,
                ofs_bank_id=tx.ofs_bank_id,
                ofs_bank_name=tx.ofs_bank_name,
                is_virtual_trans=tx.is_virtual_trans,
                virtual_acc=tx.virtual_acc,
                raw=raw_transactions[i] if i < len(raw_transactions) else {},
            )
            for i, tx in enumerate(request.data)
        ]

        logger.info(
            "tpbank.parser.parsed",
            batch_id=request.batch_id,
            transaction_count=len(transactions),
            is_uat=is_uat,
        )

        return NormalizedBatch(
            bank_id=bank_id,
            batch_id=request.batch_id,
            source_app_id=request.source_app_id,
            timestamp=request.timestamp,
            transactions=transactions,
            is_uat=is_uat,
        )
