import json
import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
import structlog
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import threading
import time

logger = structlog.get_logger()

@dataclass
class WebhookMetric:
    timestamp: str
    batch_id: str
    source_app_id: str
    transaction_count: int
    processed_count: int
    failed_count: int
    process_time: float
    status_code: int
    client_ip: str
    error_message: Optional[str] = None


class MetricsCollector:
    
    def __init__(self, db_path: str = "webhook_metrics.db"):
        self.db_path = db_path
        self.webhook_notifications_dir = Path("webhook_notifications")
        self.webhook_notifications_dir.mkdir(exist_ok=True)
        
        # In-memory caches for recent data
        self.recent_webhooks = deque(maxlen=1000)  # Last 1000 webhooks
        self.hourly_stats = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "avg_process_time": 0})
        
        # Thread safety
        self._lock = threading.Lock()
        
        # Initialize database
        self._init_database()
        
        logger.info("MetricsCollector initialized", db_path=db_path)
    
    def _init_database(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Webhook metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS webhook_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    batch_id TEXT,
                    source_app_id TEXT,
                    transaction_count INTEGER,
                    processed_count INTEGER,
                    failed_count INTEGER,
                    process_time REAL,
                    status_code INTEGER,
                    client_ip TEXT,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # UAT Webhook metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS uat_webhook_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    batch_id TEXT,
                    source_app_id TEXT,
                    transaction_count INTEGER,
                    processed_count INTEGER,
                    failed_count INTEGER,
                    process_time REAL,
                    status_code INTEGER,
                    client_ip TEXT,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes for better query performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_webhook_timestamp ON webhook_metrics(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_uat_webhook_timestamp ON uat_webhook_metrics(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_uat_webhook_source ON uat_webhook_metrics(source_app_id)')
            
            conn.commit()
            conn.close()
            
            logger.info("Khởi tạo database thành công", db_path=self.db_path)
            
        except Exception as e:
            logger.error("Khởi tạo database thất bại", error=str(e))

    def record_webhook_event(self,
                           batch_id: str,
                           source_app_id: str,
                           transaction_count: int,
                           processed_count: int,
                           failed_count: int,
                           process_time: float,
                           status_code: int,
                           client_ip: str,
                           error_message: str = None,
                           is_uat: bool = False):
        """Record a webhook processing event (supports both Production and UAT)"""
        
        metric = WebhookMetric(
            timestamp=datetime.now().isoformat(),
            batch_id=batch_id,
            source_app_id=source_app_id,
            transaction_count=transaction_count,
            processed_count=processed_count,
            failed_count=failed_count,
            process_time=process_time,
            status_code=status_code,
            client_ip=client_ip,
            error_message=error_message
        )
        
        # Only add to memory cache for production (UAT không cần cache)
        if not is_uat:
            with self._lock:
                # Add to recent cache
                self.recent_webhooks.append(metric)
                
                # Update hourly stats
                hour_key = datetime.now().strftime("%Y-%m-%d %H")
                stats = self.hourly_stats[hour_key]
                stats["total"] += 1
                if status_code == 200 and failed_count == 0:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1
                
                # Calculate rolling average process time
                if stats["total"] == 1:
                    stats["avg_process_time"] = process_time
                else:
                    stats["avg_process_time"] = (stats["avg_process_time"] * (stats["total"] - 1) + process_time) / stats["total"]
        
        # Store in database (async)
        threading.Thread(target=self._store_webhook_metric, args=(metric, is_uat), daemon=True).start()
        
        env_name = "UAT" if is_uat else "Production"
        logger.info(f"{env_name} Webhook metric recorded", 
                   batch_id=batch_id, 
                   status_code=status_code,
                   process_time=process_time,
                   environment=env_name)
    
    def _store_webhook_metric(self, metric: WebhookMetric, is_uat: bool = False):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Choose table based on environment
            table_name = "uat_webhook_metrics" if is_uat else "webhook_metrics"
            
            cursor.execute(f'''
                INSERT INTO {table_name} 
                (timestamp, batch_id, source_app_id, transaction_count, 
                 processed_count, failed_count, process_time, status_code, 
                 client_ip, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                metric.timestamp, metric.batch_id, metric.source_app_id,
                metric.transaction_count, metric.processed_count, metric.failed_count,
                metric.process_time, metric.status_code, metric.client_ip,
                metric.error_message
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            env_name = "UAT" if is_uat else "production"
            logger.error(f"Failed to store {env_name} webhook metric", error=str(e))
    
    def get_hourly_stats(self, hours: int = 24) -> Dict[str, Dict]:
        """Get hourly webhook statistics"""
        with self._lock:
            # Get recent hours
            now = datetime.now()
            recent_hours = {}
            
            for i in range(hours):
                hour = (now - timedelta(hours=i)).strftime("%Y-%m-%d %H")
                stats = self.hourly_stats.get(hour, {"total": 0, "success": 0, "failed": 0, "avg_process_time": 0})
                recent_hours[hour] = stats
            
            return recent_hours
    
    def get_recent_webhooks(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            recent = list(self.recent_webhooks)[-limit:]
            return [asdict(webhook) for webhook in recent]

    def get_webhook_metrics_from_db(self, hours: int = 24, is_uat: bool = False) -> List[Dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cutoff_time = (datetime.now() - timedelta(hours=hours)).isoformat()
            
            # Choose table based on environment
            table_name = "uat_webhook_metrics" if is_uat else "webhook_metrics"
            
            cursor.execute(f'''
                SELECT * FROM {table_name} 
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 1000
            ''', (cutoff_time,))
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            conn.close()
            
            return [dict(zip(columns, row)) for row in rows]
            
        except Exception as e:
            env_name = "UAT" if is_uat else "production"
            logger.error(f"Failed to fetch {env_name} webhook metrics from database", error=str(e))
            return []
    
    def get_summary_stats(self, is_uat: bool = False) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get stats for last 24 hours
            cutoff_time = (datetime.now() - timedelta(hours=24)).isoformat()
            
            # Choose table based on environment
            table_name = "uat_webhook_metrics" if is_uat else "webhook_metrics"
            
            # Webhook stats
            cursor.execute(f'''
                SELECT 
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status_code = 200 AND failed_count = 0 THEN 1 ELSE 0 END) as successful_requests,
                    SUM(transaction_count) as total_transactions,
                    SUM(processed_count) as processed_transactions,
                    SUM(failed_count) as failed_transactions,
                    AVG(process_time) as avg_process_time,
                    MAX(process_time) as max_process_time
                FROM {table_name} 
                WHERE timestamp >= ?
            ''', (cutoff_time,))
            
            stats = cursor.fetchone()
            conn.close()
            
            if is_uat:
                # UAT format for backward compatibility
                return {
                    "uat_requests_today": stats[0] or 0,
                    "uat_successful_requests": stats[1] or 0,
                    "uat_total_transactions": stats[2] or 0,
                    "uat_processed_transactions": stats[3] or 0,
                    "uat_failed_transactions": stats[4] or 0,
                    "uat_avg_process_time": round(stats[5] or 0, 3),
                    "uat_max_process_time": round(stats[6] or 0, 3),
                    "uat_success_rate": round((stats[1] or 0) / max(stats[0], 1) * 100, 2),
                    "last_updated": datetime.now().isoformat()
                }
            else:
                # Production format
                return {
                    "webhook": {
                        "total_requests": stats[0] or 0,
                        "successful_requests": stats[1] or 0,
                        "total_transactions": stats[2] or 0,
                        "processed_transactions": stats[3] or 0,
                        "failed_transactions": stats[4] or 0,
                        "avg_process_time": round(stats[5] or 0, 3),
                        "max_process_time": round(stats[6] or 0, 3),
                        "success_rate": round((stats[1] or 0) / max(stats[0], 1) * 100, 2)
                    },
                    "last_updated": datetime.now().isoformat()
                }
            
        except Exception as e:
            env_name = "UAT" if is_uat else "production"
            logger.error(f"Failed to get {env_name} summary stats", error=str(e))
            
            if is_uat:
                return {
                    "error": str(e),
                    "last_updated": datetime.now().isoformat()
                }
            else:
                return {
                    "webhook": {},
                    "last_updated": datetime.now().isoformat(),
                    "error": str(e)
                }
    
    def analyze_webhook_files(self) -> Dict[str, Any]:
        try:
            stats = {
                "total_files": 0,
                "total_transactions": 0,
                "transactions_by_type": defaultdict(int),
                "transactions_by_bank": defaultdict(int),
                "largest_batch": 0,
                "oldest_file": None,
                "newest_file": None
            }
            
            for date_dir in self.webhook_notifications_dir.glob("*"):
                if not date_dir.is_dir():
                    continue
                
                for webhook_file in date_dir.glob("*.json"):
                    try:
                        with open(webhook_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        stats["total_files"] += 1
                        transaction_count = data.get("transaction_count", 0)
                        stats["total_transactions"] += transaction_count
                        
                        if transaction_count > stats["largest_batch"]:
                            stats["largest_batch"] = transaction_count
                        
                        # Analyze transaction types and banks
                        for tx in data.get("data", []):
                            tx_type = tx.get("trans_type", "unknown")
                            stats["transactions_by_type"][tx_type] += 1
                            
                            bank_name = tx.get("ofs_bank_name", "unknown")
                            if bank_name != "unknown":
                                stats["transactions_by_bank"][bank_name] += 1
                        
                        # Track file timestamps
                        file_timestamp = data.get("received_at")
                        if file_timestamp:
                            if not stats["oldest_file"] or file_timestamp < stats["oldest_file"]:
                                stats["oldest_file"] = file_timestamp
                            if not stats["newest_file"] or file_timestamp > stats["newest_file"]:
                                stats["newest_file"] = file_timestamp
                    
                    except Exception as file_error:
                        logger.warning("Failed to analyze webhook file", 
                                     file=str(webhook_file), error=str(file_error))
            
            # Convert defaultdicts to regular dicts
            stats["transactions_by_type"] = dict(stats["transactions_by_type"])
            stats["transactions_by_bank"] = dict(stats["transactions_by_bank"])
            
            return stats
            
        except Exception as e:
            logger.error("Failed to analyze webhook files", error=str(e))
            return {}

# Singleton instance
_metrics_collector = None

def get_metrics_collector() -> MetricsCollector:
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector