"""
Internal normalized domain models.

All bank-specific payload parsers MUST produce these models.
This decouples the rest of the pipeline from any particular bank's wire format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class NormalizedTransaction:
    """Bank-agnostic transaction record used throughout the processing pipeline."""

    # Identifiers
    transaction_id: str
    bank_id: str            # e.g. "tpbank", "bidv", "vcb"
    batch_id: str

    # Financial
    amount: float
    trans_type: str         # "D" = debit, "C" = credit  (normalized across banks)

    # Account info
    src_account_number: str

    # Optional fields (may not exist for all banks)
    tran_refno: Optional[str] = None
    balance_available: Optional[float] = None
    notice_date_time: Optional[str] = None
    trans_time: Optional[str] = None
    trans_desc: Optional[str] = None
    ofs_account_number: Optional[str] = None
    ofs_account_name: Optional[str] = None
    ofs_bank_id: Optional[str] = None
    ofs_bank_name: Optional[str] = None
    is_virtual_trans: Optional[str] = None
    virtual_acc: Optional[str] = None

    # Metadata
    received_at: datetime = field(default_factory=datetime.now)
    raw: Dict[str, Any] = field(default_factory=dict)   # original parsed dict for archival


@dataclass
class NormalizedBatch:
    """A batch of transactions from a bank webhook call."""

    bank_id: str
    batch_id: str
    source_app_id: str
    timestamp: str
    transactions: List[NormalizedTransaction]
    is_uat: bool = False
    received_at: datetime = field(default_factory=datetime.now)


@dataclass
class TransactionProcessingResult:
    """Result for a single transaction after processing."""

    transaction_id: str
    success: bool
    error_code: str = "00"          # "00" = ok, "01" = error, "02" = duplicate
    description: str = "Giao dịch thành công"
    additional_info: Optional[Dict[str, Any]] = None


@dataclass
class BatchProcessingResult:
    """Aggregate result for an entire batch."""

    batch_id: str
    bank_id: str
    total: int
    processed_count: int
    failed_count: int
    success: bool
    transaction_results: List[TransactionProcessingResult] = field(default_factory=list)
    error: Optional[str] = None
