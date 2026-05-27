from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TransactionData(BaseModel):
    model_config = {"populate_by_name": True}

    transaction_id: str = Field(alias="transactionId")
    tran_refno: str = Field(alias="tranRefNo")                      # Y per docs
    account_number: str = Field(alias="accountNumber")
    amount: float = Field(alias="amount")
    trans_type: Optional[str] = Field(None, alias="transType")       # N per docs
    balance_available: str = Field(alias="balanceAvailable")         # Y per docs (string in example)
    noti_created_time: str = Field(alias="notiCreatedTime")          # Y per docs
    trans_time: str = Field(alias="transTime")                       # Y per docs
    tran_desc: Optional[str] = Field(None, alias="tranDesc")
    ofs_account_number: Optional[str] = Field(None, alias="ofsAccountNumber")
    ofs_account_name: Optional[str] = Field(None, alias="ofsAccountName")
    ofs_bank_id: Optional[str] = Field(None, alias="ofsBankId")
    ofs_bank_name: Optional[str] = Field(None, alias="ofsBankName")
    is_virtual_trans: Optional[str] = Field(None, alias="isVirtualTrans")
    virtual_acc: Optional[str] = Field(None, alias="virtualAcc")


class WebhookRequest(BaseModel):
    model_config = {"populate_by_name": True}

    source_app_id: str = Field(alias="sourceAppId")
    batch_id: str = Field(alias="batchId")
    timestamp: str = Field(alias="timestamp")
    signature: str = Field(alias="signature")
    data: List[TransactionData] = Field(alias="data")



