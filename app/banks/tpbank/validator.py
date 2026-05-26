from __future__ import annotations

from typing import List

import structlog

from app.banks.base import TransactionValidator
from app.core.models import NormalizedTransaction

logger = structlog.get_logger()


class TPBankValidator(TransactionValidator):
    def validate(self, tx: NormalizedTransaction) -> List[str]:
        errors: List[str] = []

        if not tx.transaction_id or len(tx.transaction_id) < 10:
            errors.append("Mã giao dịch không hợp lệ hoặc quá ngắn (tối thiểu 10 ký tự)")

        if tx.amount <= 0:
            errors.append("Số tiền giao dịch phải lớn hơn 0")

        if not tx.src_account_number or len(tx.src_account_number) < 8:
            errors.append("Số tài khoản nguồn không hợp lệ hoặc quá ngắn (tối thiểu 8 ký tự)")

        if tx.trans_type not in ("D", "C"):
            errors.append(f"Loại giao dịch không hợp lệ: '{tx.trans_type}'. Chỉ chấp nhận 'D' hoặc 'C'")

        if tx.balance_available is not None and tx.balance_available < 0:
            errors.append("Số dư khả dụng không được âm")

        if errors:
            logger.warning(
                "tpbank.validator.failed",
                transaction_id=tx.transaction_id,
                errors=errors,
            )

        return errors
