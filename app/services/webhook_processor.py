from typing import Dict, Any, List
import structlog
from datetime import datetime, timedelta
import asyncio
import json
import os
import sqlite3
from pathlib import Path

from app.models import WebhookRequest, TransactionData

logger = structlog.get_logger()


class WebhookProcessor:

    def __init__(self, db_path: str = "webhook_metrics.db"):
        self.db_path = db_path
        # Separate in-memory caches for Production and UAT
        self.processed_transactions = set()  # Production only
        self.processed_transactions_uat = set()  # UAT only
        
        # Setup webhook storage directory
        self.webhook_storage_dir = Path("webhook_notifications")
        self.webhook_storage_dir.mkdir(exist_ok=True)
        
        # Initialize persistent storage for processed transactions
        self._init_processed_transactions_db()
        
        # Load processed transactions from database into memory (Production only)
        self._load_processed_transactions()
        
        logger.info("Khởi tạo WebhookProcessor thành công", 
                   storage_dir=str(self.webhook_storage_dir),
                   loaded_production_transactions=len(self.processed_transactions),
                   loaded_uat_transactions=len(self.processed_transactions_uat))
    
    def _init_processed_transactions_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Enhanced table to store complete transaction information
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    
                    -- Core transaction fields
                    tran_refno TEXT NOT NULL,
                    account_number TEXT NOT NULL,
                    amount REAL NOT NULL,
                    balance_available REAL,
                    trans_type TEXT NOT NULL,
                    
                    -- Time fields
                    notice_date_time TEXT,
                    trans_time TEXT,
                    
                    -- Description fields
                    trans_desc TEXT,
                    
                    -- Offset account information
                    ofs_account_number TEXT,
                    ofs_account_name TEXT,
                    ofs_bank_id TEXT,
                    ofs_bank_name TEXT,
                    
                    -- Virtual transaction fields
                    is_virtual_trans TEXT,
                    virtual_acc TEXT,
                    
                    -- Metadata
                    source_app_id TEXT,
                    webhook_timestamp TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Indexes for performance
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_processed_transactions_processed_at 
                ON processed_transactions(processed_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_processed_transactions_batch_id 
                ON processed_transactions(batch_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_processed_transactions_account_number 
                ON processed_transactions(account_number)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_processed_transactions_amount 
                ON processed_transactions(amount)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_processed_transactions_virtual_trans 
                ON processed_transactions(is_virtual_trans)
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Database table 'processed_transactions' với schema đầy đủ đã được khởi tạo")
            
        except Exception as e:
            logger.error("Lỗi khởi tạo database table processed_transactions", error=str(e))
            raise
    
    def _load_processed_transactions(self):
        """Load all processed transaction IDs from database into memory cache"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Load all processed transactions (không giới hạn thời gian để bảo vệ dữ liệu)
            cursor.execute('''
                SELECT transaction_id FROM processed_transactions 
                ORDER BY processed_at DESC LIMIT 1000
            ''')
            
            rows = cursor.fetchall()
            self.processed_transactions = {row[0] for row in rows}
            
            conn.close()
            logger.info(f"Đã load {len(self.processed_transactions)} processed transactions từ database (tất cả records)")
            
        except Exception as e:
            logger.error("Lỗi load processed transactions từ database", error=str(e))
            self.processed_transactions = set()  # Fallback to empty set
    
    async def _save_processed_transaction(self, transaction_data: TransactionData, batch_id: str, source_app_id: str, webhook_timestamp: str):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO processed_transactions 
                (transaction_id, batch_id, processed_at, tran_refno, account_number, 
                 amount, balance_available, trans_type, notice_date_time, trans_time, 
                 trans_desc, ofs_account_number, ofs_account_name, ofs_bank_id, 
                 ofs_bank_name, is_virtual_trans, virtual_acc, source_app_id, webhook_timestamp) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                transaction_data.transaction_id,
                batch_id,
                datetime.now().isoformat(),
                transaction_data.tran_refno,
                transaction_data.src_account_number,
                transaction_data.amount,
                transaction_data.balance_available,
                transaction_data.trans_type,
                transaction_data.notice_date_time,
                transaction_data.trans_time,
                transaction_data.trans_desc,
                transaction_data.ofs_account_number,
                transaction_data.ofs_account_name,
                transaction_data.ofs_bank_id,
                transaction_data.ofs_bank_name,
                transaction_data.is_virtual_trans,
                transaction_data.virtual_acc,
                source_app_id,
                webhook_timestamp
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error("Lỗi lưu processed transaction vào database", 
                        transaction_id=transaction_data.transaction_id, 
                        batch_id=batch_id,
                        error=str(e))
    
    async def process_notification(self, webhook_data: WebhookRequest, is_uat: bool = False) -> Dict[str, Any]:
        try:
            # Save webhook notification to file first (UAT or Production)
            await self._save_webhook_to_file(webhook_data, is_uat=is_uat)
            
            # Log incoming notification batch
            env_prefix = "UAT: " if is_uat else ""
            logger.info(
                f"{env_prefix} - Xử lý webhook batch",
                batch_id=webhook_data.batch_id,
                source_app_id=webhook_data.source_app_id,
                transaction_count=len(webhook_data.data),
                timestamp=webhook_data.timestamp,
                environment="UAT" if is_uat else "Production"
            )
            
            processed_count = 0
            failed_transactions = []
            
            # Process each transaction in the batch
            for transaction in webhook_data.data:
                try:
                    # Check for duplicate transaction (use appropriate cache based on environment)
                    duplicate_cache = self.processed_transactions_uat if is_uat else self.processed_transactions
                    if transaction.transaction_id in duplicate_cache:
                        logger.warning(
                            f"{env_prefix} Phát hiện giao dịch trùng lặp, bỏ qua",
                            transaction_id=transaction.transaction_id,
                            batch_id=webhook_data.batch_id,
                            environment="UAT" if is_uat else "Production"
                        )
                        failed_transactions.append({
                            "transaction_id": transaction.transaction_id,
                            "error": "Giao dịch trùng lặp"
                        })
                        continue
                    
                    # Validate transaction data
                    validation_result = await self._validate_transaction_data(transaction)
                    if not validation_result["valid"]:
                        logger.error(
                            f"{env_prefix} - Lỗi dữ liệu giao dịch không hợp lệ (process_notification_validation)",
                            transaction_id=transaction.transaction_id,
                            batch_id=webhook_data.batch_id,
                            errors=validation_result["errors"],
                        )
                        failed_transactions.append({
                            "transaction_id": transaction.transaction_id,
                            "error": f"Validation failed: {', '.join(validation_result['errors'])}"
                        })
                        continue
                    
                    # Process the individual transaction
                    processing_result = await self._process_transaction(transaction, webhook_data.batch_id, is_uat=is_uat)
                    if not processing_result["success"]:
                        logger.error(
                            f"{env_prefix} - Lỗi xử lý transaction (process_notification_processing)",
                            transaction_id=transaction.transaction_id,
                            batch_id=webhook_data.batch_id,
                            error=processing_result["error"]
                        )
                        failed_transactions.append({
                            "transaction_id": transaction.transaction_id,
                            "error": processing_result["error"]
                        })
                        continue
                    
                    # Mark transaction as processed - save to appropriate cache and database
                    if is_uat:
                        # For UAT: only save to UAT cache, no database persistence
                        self.processed_transactions_uat.add(transaction.transaction_id)
                    else:
                        # For Production: save to both cache and database
                        self.processed_transactions.add(transaction.transaction_id)
                        await self._save_processed_transaction(
                            transaction, 
                            webhook_data.batch_id, 
                            webhook_data.source_app_id, 
                            webhook_data.timestamp
                        )
                    
                    processed_count += 1
                    
                except Exception as e:
                    logger.error(
                        f"{env_prefix} - Lỗi xử lý transaction (process_notification trace2)",
                        transaction_id=transaction.transaction_id,
                        batch_id=webhook_data.batch_id,
                        error=str(e),
                        environment="UAT" if is_uat else "Production",
                        exc_info=True
                    )
                    failed_transactions.append({
                        "transaction_id": transaction.transaction_id,
                        "error": f"Processing exception: {str(e)}"
                    })
            
            # Determine overall success
            total_transactions = len(webhook_data.data)
            success_rate = processed_count / total_transactions if total_transactions > 0 else 0
            
            logger.info(
                f"{env_prefix}Xử lý webhook batch hoàn tất",
                batch_id=webhook_data.batch_id,
                total_transactions=total_transactions,
                processed_count=processed_count,
                failed_count=len(failed_transactions),
                success_rate=success_rate,
                environment="UAT" if is_uat else "Production"
            )
            
            # Build response
            base_response = {
                "success": len(failed_transactions) == 0,  # Success only if all transactions processed
                "processed_count": processed_count,
                "failed_count": len(failed_transactions),
                "failed_transactions": failed_transactions,
                "batch_id": webhook_data.batch_id
            }
            
            return base_response
            
        except Exception as e:
            env_prefix = "UAT: " if is_uat else ""
            error_code = "UAT_BATCH_PROCESSING_ERROR" if is_uat else "BATCH_PROCESSING_ERROR"
            
            logger.error(
                f"{env_prefix}Webhook batch xử lý lỗi",
                batch_id=getattr(webhook_data, 'batch_id', 'unknown'),
                error=str(e),
                environment="UAT" if is_uat else "Production",
                exc_info=True
            )
            
            base_response = {
                "success": False,
                "error": f"{env_prefix} - Batch processing error",
                "error_code": error_code,
                "processed_count": 0
            }
                
            return base_response

    def get_processed_transactions_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about processed transactions and service status"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Count total records in database
            cursor.execute('SELECT COUNT(*) FROM processed_transactions')
            total_in_db = cursor.fetchone()[0]
            
            # Count by date ranges
            now = datetime.now()
            today = now.date().isoformat()
            last_7_days = (now - timedelta(days=7)).isoformat()
            last_30_days = (now - timedelta(days=30)).isoformat()
            
            cursor.execute('SELECT COUNT(*) FROM processed_transactions WHERE notice_date_time >= ?', (today,))
            today_count = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM processed_transactions WHERE notice_date_time >= ?', (last_7_days,))
            week_count = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM processed_transactions WHERE notice_date_time >= ?', (last_30_days,))
            month_count = cursor.fetchone()[0]
            
            # Amount statistics for last 30 days
            cursor.execute('''
                SELECT 
                    SUM(amount) as total_amount,
                    AVG(amount) as avg_amount,
                    MIN(amount) as min_amount,
                    MAX(amount) as max_amount,
                    COUNT(DISTINCT account_number) as unique_accounts,
                    COUNT(DISTINCT batch_id) as unique_batches
                FROM processed_transactions 
                WHERE notice_date_time >= ?
            ''', (last_30_days,))
            
            amount_stats = cursor.fetchone()
            
            # Transaction type breakdown for last 7 days
            cursor.execute('''
                SELECT 
                    trans_type,
                    COUNT(*) as count,
                    SUM(amount) as total_amount
                FROM processed_transactions 
                WHERE notice_date_time >= ?
                GROUP BY trans_type
            ''', (last_7_days,))
            
            type_breakdown = {}
            for row in cursor.fetchall():
                trans_type = row[0]
                type_breakdown[trans_type] = {
                    "count": row[1],
                    "total_amount": round(row[2] or 0, 2)
                }
            
            # Recent activity (last 24 hours)
            last_24_hours = (now - timedelta(hours=24)).isoformat()
            cursor.execute('''
                SELECT 
                    COUNT(*) as recent_count,
                    SUM(amount) as recent_amount,
                    COUNT(DISTINCT batch_id) as recent_batches
                FROM processed_transactions 
                WHERE notice_date_time >= ?
            ''', (last_24_hours,))
            
            recent_stats = cursor.fetchone()
            conn.close()
            
            return {
                # Service status
                "service_status": "healthy" if total_in_db > 0 else "no_data",                
                # Database vs Memory sync (Production only)
                "storage": {
                    "total_in_memory_production": len(self.processed_transactions),
                    "total_in_memory_uat": len(self.processed_transactions_uat),
                    "total_in_database": total_in_db,
                    "sync_status": "synced" if len(self.processed_transactions) == total_in_db else "out_of_sync",
                    "sync_difference": total_in_db - len(self.processed_transactions)
                },
                # Time-based counts
                "counts_by_period": {
                    "today": today_count,
                    "last_7_days": week_count,
                    "last_30_days": month_count,
                    "last_24_hours": recent_stats[0] or 0
                },
                
                # Financial statistics (last 30 days)
                "financial_stats_30_days": {
                    "total_amount": round(amount_stats[0] or 0, 2),
                    "average_amount": round(amount_stats[1] or 0, 2),
                    "min_amount": round(amount_stats[2] or 0, 2),
                    "max_amount": round(amount_stats[3] or 0, 2),
                    "unique_accounts": amount_stats[4] or 0,
                    "unique_batches": amount_stats[5] or 0
                },
                
                # Transaction type breakdown (last 7 days)
                "transaction_types_7_days": type_breakdown,
                
                # Recent activity (last 24 hours)
                "recent_activity_24h": {
                    "transaction_count": recent_stats[0] or 0,
                    "total_amount": round(recent_stats[1] or 0, 2),
                    "batch_count": recent_stats[2] or 0
                },
                
                # Metadata
                "last_updated": now.isoformat(),
                "database_path": self.db_path
            }
            
        except Exception as e:
            logger.error("Lỗi lấy stats processed transactions", error=str(e))
            return {
                "service_status": "error",
                "error": str(e),
                "storage": {
                    "total_in_memory_production": len(self.processed_transactions),
                    "total_in_memory_uat": len(self.processed_transactions_uat),
                    "total_in_database": 0,
                    "sync_status": "error"
                },
                "last_updated": datetime.now().isoformat()
            }

    async def _validate_transaction_data(self, transaction_data: TransactionData) -> Dict[str, Any]:

        errors = []
        
        # Validate transaction ID format
        if not transaction_data.transaction_id or len(transaction_data.transaction_id) < 10:
            errors.append("Transaction ID không hợp lệ")
        
        # Validate amount
        if transaction_data.amount <= 0:
            errors.append("Số tiền giao dịch phải dương")
        
        # Validate account number format (basic validation)
        if not transaction_data.src_account_number or len(transaction_data.src_account_number) < 8:
            errors.append("Định dạng số tài khoản nguồn không hợp lệ")

        # Validate transaction type
        valid_types = ["D", "C"]  # Debit, Credit
        if transaction_data.trans_type not in valid_types:
            errors.append(f"Loại giao dịch không hợp lệ. Phải là một trong số: {', '.join(valid_types)}")
        
        # Validate balance if provided
        if transaction_data.balance_available is not None and transaction_data.balance_available < 0:
            errors.append("Số dư khả dụng không được âm")

        return {
            "valid": True,
            "errors": errors
        }
    
    async def _process_transaction(self, transaction_data: TransactionData, batch_id: str, is_uat: bool = False) -> Dict[str, Any]:
        start_time = datetime.now()
        env_prefix = "UAT: " if is_uat else ""
        
        try:            
            processing_result = await self._simulate_business_logic(transaction_data, batch_id, is_uat=is_uat)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            logger.info(
                f"{env_prefix}Xử lý logic hoàn tất (_process_transaction)",
                transaction_id=transaction_data.transaction_id,
                batch_id=batch_id,
                processing_time=processing_time,
                result=processing_result["status"],
                environment="UAT" if is_uat else "Production"
            )
            
            return {
                "success": True,
                "processing_time": processing_time,
                "business_result": processing_result
            }
            
        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.error(
                f"{env_prefix}Lỗi xử lý logic (_process_transaction)",
                transaction_id=transaction_data.transaction_id,
                batch_id=batch_id,
                error=str(e),
                processing_time=processing_time,
                environment="UAT" if is_uat else "Production"
            )
            
            return {
                "success": False,
                "error": f"{env_prefix}Lỗi xử lý (_process_transaction): {str(e)}",
                "processing_time": processing_time
            }
    
    async def _simulate_business_logic(self, transaction_data: TransactionData, batch_id: str, is_uat: bool = False) -> Dict[str, Any]:
        # Simulate different processing based on transaction type
        # UAT and Production use same logic but can be differentiated for testing
        
        base_result = {
            "batch_id": batch_id,
            "environment": "UAT" if is_uat else "Production"
        }
        
        if transaction_data.trans_type == "C":  # Credit
            base_result.update({
                "status": "credit_processed",
                "account_balance_updated": True,
                "notification_sent": True
            })
        elif transaction_data.trans_type == "D":  # Debit
            base_result.update({
                "status": "debit_processed", 
                "account_balance_updated": True,
                "notification_sent": True
            })
        else:
            base_result.update({
                "status": "unknown_type_processed",
                "account_balance_updated": False,
                "notification_sent": False
            })
        
        # Add UAT-specific testing metadata if needed
        if is_uat:
            base_result["test_metadata"] = {
                "simulated": True,
                "test_environment": True
            }
        
        return base_result
    
    async def _save_webhook_to_file(self, webhook_data: WebhookRequest, is_uat: bool = False):
        try:
            # Setup storage directory based on environment
            if is_uat:
                storage_dir = Path("webhook_notifications_uat")
                env_prefix = "UAT_"
                env_name = "UAT"
            else:
                storage_dir = self.webhook_storage_dir
                env_prefix = ""
                env_name = "Production"
            
            storage_dir.mkdir(exist_ok=True)
            
            # Create filename with timestamp and batch_id
            timestamp = datetime.now()
            date_folder = storage_dir / timestamp.strftime("%Y%m%d")
            date_folder.mkdir(exist_ok=True)
            
            # Filename format: [UAT_]YYYYMMDD_HHMMSS_batchId.json
            filename = f"{env_prefix}{timestamp.strftime('%Y%m%d_%H%M%S')}_{webhook_data.batch_id}.json"
            file_path = date_folder / filename
            
            # Base webhook data structure
            webhook_dict = {
                "received_at": timestamp.isoformat(),
                "batch_id": webhook_data.batch_id,
                "source_app_id": webhook_data.source_app_id,
                "timestamp": webhook_data.timestamp,
                "data": [
                    {
                        "transaction_id": tx.transaction_id,
                        "tran_refno": tx.tran_refno,
                        "src_account_number": tx.src_account_number,
                        "amount": tx.amount,
                        "balance_available": tx.balance_available,
                        "trans_type": tx.trans_type,
                        "notice_date_time": tx.notice_date_time,
                        "trans_time": tx.trans_time,
                        "trans_desc": tx.trans_desc,
                        "ofs_account_number": tx.ofs_account_number,
                        "ofs_account_name": tx.ofs_account_name,
                        "ofs_bank_id": tx.ofs_bank_id,
                        "ofs_bank_name": tx.ofs_bank_name,
                        "is_virtual_trans": tx.is_virtual_trans,
                        "virtual_acc": tx.virtual_acc
                    }
                    for tx in webhook_data.data
                ],
                "transaction_count": len(webhook_data.data)
            }
            
            # Write to file with pretty formatting
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(webhook_dict, f, indent=2, ensure_ascii=False)
            
            logger.info(
                f"Đã lưu {env_name} backup webhook notification",
                batch_id=webhook_data.batch_id,
                file_path=str(file_path),
                transaction_count=len(webhook_data.data),
                environment=env_name,
                storage_dir=str(storage_dir)
            )
            
        except Exception as e:
            # Don't fail the entire process if file saving fails
            logger.error(
                f"Lỗi khi lưu {env_name} backup webhook notification",
                batch_id=getattr(webhook_data, 'batch_id', 'unknown'),
                error=str(e),
                exc_info=True
            )