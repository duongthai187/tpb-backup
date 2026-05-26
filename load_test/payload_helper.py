"""Helper độc lập — gen và ký payload TPBank, không import locust."""
import base64
import json
import os
import random
import string
import uuid
from datetime import datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

KEY_DIR = os.path.join(os.path.dirname(__file__), "test_keys")
PRIVATE_KEY_PATH = os.path.join(KEY_DIR, "test_private.pem")


def load_private_key():
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


_private_key = load_private_key()


def sign(source_app_id: str, batch_id: str, timestamp: str) -> str:
    canonical = (source_app_id + batch_id + timestamp).encode()
    sig = _private_key.sign(canonical, padding.PKCS1v15(), hashes.SHA512())
    return base64.b64encode(sig).decode()


def make_payload(num_transactions: int = 1) -> dict:
    source_app_id = "TPBANK_WEBHOOK"
    batch_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    transactions = []
    for _ in range(num_transactions):
        tx_id = str(uuid.uuid4())
        transactions.append({
            "transactionId": tx_id,
            "tranRefNo": f"REF{random.randint(100000,999999)}",
            "accountNumber": "".join(random.choices(string.digits, k=10)),
            "amount": round(random.uniform(10_000, 100_000_000), 0),
            "transType": random.choice(["C", "D"]),
            "balanceAvailable": round(random.uniform(10_000, 500_000_000), 0),
            "notiCreatedTime": datetime.utcnow().isoformat(),
            "transTime": datetime.utcnow().isoformat(),
            "tranDesc": f"Load test {tx_id[:8]}",
            "ofsAccountNumber": "".join(random.choices(string.digits, k=10)),
            "ofsAccountName": f"Test User {random.randint(1,999)}",
            "ofsBankId": random.choice(["VCB", "TCB", "MBB", "ACB"]),
            "ofsBankName": "Test Bank",
            "isVirtualTrans": "N",
            "virtualAcc": None,
        })
    return {
        "sourceAppId": source_app_id,
        "batchId": batch_id,
        "timestamp": timestamp,
        "signature": sign(source_app_id, batch_id, timestamp),
        "data": transactions,
    }
