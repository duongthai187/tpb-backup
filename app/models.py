from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class TransactionData(BaseModel):
    """Model for individual transaction data"""
    transaction_id: str = Field(..., alias="transactionId", description="Mã giao dịch duy nhất")
    tran_refno: str = Field(..., alias="tranRefNo", description="Số tham chiếu giao dịch")
    src_account_number: str = Field(..., alias="accountNumber", description="Số tài khoản nguồn")
    amount: float = Field(..., description="Số tiền giao dịch")
    balance_available: Optional[float] = Field(None, alias="balanceAvailable", description="Số dư khả dụng")
    trans_type: str = Field(..., alias="transType", description="Loại giao dịch (D/C)")
    
    # Optional fields
    notice_date_time: Optional[str] = Field(None, alias="notiCreatedTime", description="Thời gian thông báo")
    trans_time: Optional[str] = Field(None, alias="transTime", description="Thời gian giao dịch")
    trans_desc: Optional[str] = Field(None, alias="tranDesc", description="Mô tả giao dịch")
    ofs_account_number: Optional[str] = Field(None, alias="ofsAccountNumber", description="Số tài khoản đối ứng")
    ofs_account_name: Optional[str] = Field(None, alias="ofsAccountName", description="Tên tài khoản đối ứng")
    ofs_bank_id: Optional[str] = Field(None, alias="ofsBankId", description="Mã ngân hàng đối ứng")
    ofs_bank_name: Optional[str] = Field(None, alias="ofsBankName", description="Tên ngân hàng đối ứng")
    is_virtual_trans: Optional[str] = Field(None, alias="isVirtualTrans", description="Giao dịch ảo")
    virtual_acc: Optional[str] = Field(None, alias="virtualAcc", description="Tài khoản ảo")

    class Config:
        populate_by_name = True


class WebhookRequest(BaseModel):
    """Model for incoming webhook data from bank"""
    source_app_id: str = Field(..., alias="sourceAppId", description="Mã ứng dụng nguồn")
    batch_id: str = Field(..., alias="batchId", description="Mã batch giao dịch")
    timestamp: str = Field(..., description="Thời gian gửi request")
    signature: str = Field(..., description="Chữ ký điện tử SHA512withRSA")
    data: List[TransactionData] = Field(..., description="Danh sách giao dịch")
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TransactionResult(BaseModel):
    """Model for individual transaction result"""
    transaction_id: str = Field(..., alias="transactionId", description="Mã giao dịch")
    error_code: str = Field(..., alias="errorCode", description="Mã lỗi: 01-thành công, 02-thất bại, 03-thất bại có resend, 04-thất bại có lý do")
    description: str = Field(..., description="Thông tin trạng thái")
    additional_info: Optional[Dict[str, Any]] = Field(default_factory=dict, alias="additionalInfo", description="Thông tin bổ sung")
    
    class Config:
        populate_by_name = True


class WebhookResponse(BaseModel):
    """Response model for webhook endpoint"""
    batch_id: str = Field(..., alias="batchId", description="Mã batch để tham chiếu")
    code: str = Field(..., description="Mã phản hồi: 200-Thành công")
    message: Optional[str] = Field(None, description="Thông điệp phản hồi")
    data: List[TransactionResult] = Field(..., description="Danh sách kết quả từng giao dịch")
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
