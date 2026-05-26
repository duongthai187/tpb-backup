"""
Message processor — converts a Redis Stream message into Postgres rows.

Stream message fields:
  bank_id     : str
  batch_id    : str
  received_at : ISO datetime string
  payload     : full JSON string of the persisted batch

The payload structure (set by tpb-backup/app/core/processor.py):
  {
    "received_at": "...",
    "bank_id": "tpbank",
    "batch_id": "...",
    "source_app_id": "...",
    "timestamp": "...",
    "is_uat": false,
    "transaction_count": N,
    "transactions": [...]     ← correct key (was "data" bug — now fixed)
  }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from .db import Database
from .utils import parse_datetime, parse_decimal

LOGGER = logging.getLogger(__name__)


class MessageProcessor:
    def __init__(self, database: Database, worker_name: str = "worker") -> None:
        self.database = database
        self.worker_name = worker_name

    def process(self, fields: Dict[str, str]) -> None:
        """
        Process one Redis Stream message.
        Raises on failure so the caller keeps the message in PEL for retry.
        """
        raw_payload = fields.get("payload", "")
        if not raw_payload:
            LOGGER.error("Empty payload field in stream message: %s", fields)
            return  # nothing to retry — ack upstream

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            LOGGER.error("Cannot decode payload JSON: %s", exc)
            raise  # keep in PEL

        rows = self._build_rows(payload)
        if not rows:
            LOGGER.warning(
                "No rows built from batch %s (bank=%s) — transactions list empty?",
                payload.get("batch_id"),
                payload.get("bank_id"),
            )
            return  # ack — nothing to insert

        inserted = self.database.insert_transactions(rows)
        LOGGER.info(
            "Inserted %s rows | worker=%s batch=%s bank=%s",
            inserted,
            self.worker_name,
            payload.get("batch_id"),
            payload.get("bank_id"),
        )

    def _build_rows(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        envelope = {
            "received_at": parse_datetime(payload.get("received_at")),
            "bank_id": payload.get("bank_id"),
            "batch_id": payload.get("batch_id"),
            "source_app_id": payload.get("source_app_id"),
            "payload_timestamp": parse_datetime(payload.get("timestamp")),
            "transaction_count": payload.get("transaction_count"),
        }

        # ── Key fix: tpb-backup saves key "transactions", not "data" ──────────
        tx_list = payload.get("transactions") or []
        if not isinstance(tx_list, list):
            tx_list = [tx_list]

        rows: List[Dict[str, Any]] = []
        for entry in tx_list:
            if not isinstance(entry, dict):
                LOGGER.warning("Skipping non-dict transaction entry: %s", type(entry))
                continue
            row = {
                **envelope,
                "transaction_id": entry.get("transaction_id"),
                "tran_refno": entry.get("tran_refno"),
                "src_account_number": entry.get("src_account_number"),
                "amount": parse_decimal(entry.get("amount")),
                "balance_available": parse_decimal(entry.get("balance_available")),
                "trans_type": entry.get("trans_type"),
                "notice_datetime": parse_datetime(
                    entry.get("notice_date_time") or entry.get("notice_datetime")
                ),
                "trans_time": entry.get("trans_time"),
                "trans_desc": entry.get("trans_desc"),
                "ofs_account_number": entry.get("ofs_account_number"),
                "ofs_account_name": entry.get("ofs_account_name"),
                "ofs_bank_id": entry.get("ofs_bank_id"),
                "ofs_bank_name": entry.get("ofs_bank_name"),
                "is_virtual_trans": entry.get("is_virtual_trans"),
                "virtual_acc": entry.get("virtual_acc"),
                "transaction_json": entry,
            }
            rows.append(row)

        return rows
