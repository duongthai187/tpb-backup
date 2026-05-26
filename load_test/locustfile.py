"""
Locust Load & Stress Test — TPBank Webhook Hub
===============================================
Chạy:
    locust -f locustfile.py --host http://localhost:8443

Hoặc headless:
    locust -f locustfile.py --host http://localhost:8443 \
        --users 50 --spawn-rate 5 --run-time 2m --headless

Lưu ý: script tự gen RSA key pair, dùng PRIVATE key để ký,
        cần swap PUBLIC key trên server trước khi test.
        Chạy:  python locustfile.py --setup  để gen key pair.
"""

import base64
import json
import os
import random
import string
import time
import uuid
from datetime import datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

# ── Key paths ─────────────────────────────────────────────────────────────────
KEY_DIR = os.path.join(os.path.dirname(__file__), "test_keys")
PRIVATE_KEY_PATH = os.path.join(KEY_DIR, "test_private.pem")
PUBLIC_KEY_PATH = os.path.join(KEY_DIR, "test_public.pem")


def _gen_or_load_keys():
    """Gen RSA-2048 key pair nếu chưa có."""
    os.makedirs(KEY_DIR, exist_ok=True)
    if not os.path.exists(PRIVATE_KEY_PATH):
        print("[setup] Generating RSA-2048 test key pair...")
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with open(PRIVATE_KEY_PATH, "wb") as f:
            f.write(priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        with open(PUBLIC_KEY_PATH, "wb") as f:
            f.write(priv.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ))
        print(f"[setup] Keys saved to {KEY_DIR}/")
        print(f"[setup] Copy public key to server:")
        print(f"        scp {PUBLIC_KEY_PATH} 192.168.255.71:/opt/tpb-webhook/certs/bank_public.pem")
    else:
        print(f"[setup] Using existing keys from {KEY_DIR}/")

    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    return private_key


# Load key khi module được import
_private_key = _gen_or_load_keys()


def _sign(source_app_id: str, batch_id: str, timestamp: str) -> str:
    """Ký canonical string: sourceAppId + batchId + timestamp (RSA-PKCS1v15-SHA512)."""
    canonical = (source_app_id + batch_id + timestamp).encode("utf-8")
    sig = _private_key.sign(canonical, padding.PKCS1v15(), hashes.SHA512())
    return base64.b64encode(sig).decode()


def _rand_account() -> str:
    return "".join(random.choices(string.digits, k=10))


def _rand_amount() -> float:
    return round(random.uniform(10_000, 100_000_000), 0)


def _make_payload(num_transactions: int = 1) -> dict:
    """Build một TPBank webhook payload hợp lệ."""
    source_app_id = "TPBANK_WEBHOOK"
    batch_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    transactions = []
    for _ in range(num_transactions):
        tx_id = str(uuid.uuid4())
        transactions.append({
            "transactionId": tx_id,
            "tranRefNo": f"REF{random.randint(100000, 999999)}",
            "accountNumber": _rand_account(),
            "amount": _rand_amount(),
            "transType": random.choice(["C", "D"]),
            "balanceAvailable": _rand_amount(),
            "notiCreatedTime": datetime.utcnow().isoformat(),
            "transTime": datetime.utcnow().isoformat(),
            "tranDesc": f"Load test tx {tx_id[:8]}",
            "ofsAccountNumber": _rand_account(),
            "ofsAccountName": f"Test User {random.randint(1,999)}",
            "ofsBankId": random.choice(["VCB", "TCB", "MBB", "ACB"]),
            "ofsBankName": "Test Bank",
            "isVirtualTrans": "N",
            "virtualAcc": None,
        })

    signature = _sign(source_app_id, batch_id, timestamp)

    return {
        "sourceAppId": source_app_id,
        "batchId": batch_id,
        "timestamp": timestamp,
        "signature": signature,
        "data": transactions,
    }


# ── Locust Users ──────────────────────────────────────────────────────────────

class WebhookUser(HttpUser):
    """Simulate TPBank sending webhook notifications — normal load."""
    wait_time = between(0.1, 0.5)  # 2–10 req/s per user

    @task(10)
    def post_single_transaction(self):
        """POST 1 transaction — most common case."""
        payload = _make_payload(num_transactions=1)
        with self.client.post(
            "/webhook/tpbank/notification",
            json=payload,
            headers={"Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/tpbank/notification [1tx]",
        ) as resp:
            body = resp.json()
            if body.get("code") not in ("200", "00", None) and body.get("code") != 200:
                # code "401" = signature fail (expected if key not swapped)
                if body.get("code") == "401":
                    resp.failure(f"Signature rejected: {body.get('message')}")
                elif body.get("code") in ("400", "500"):
                    resp.failure(f"App error {body['code']}: {body.get('message')}")
                else:
                    resp.success()
            else:
                resp.success()

    @task(3)
    def post_batch_transactions(self):
        """POST batch 5 transactions."""
        payload = _make_payload(num_transactions=5)
        with self.client.post(
            "/webhook/tpbank/notification",
            json=payload,
            headers={"Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/tpbank/notification [5tx]",
        ) as resp:
            body = resp.json()
            if body.get("code") == "401":
                resp.failure(f"Signature rejected — need to swap public key first!")
            else:
                resp.success()

    @task(2)
    def post_uat(self):
        """POST UAT endpoint — same pipeline, extra debug_info."""
        payload = _make_payload(num_transactions=1)
        with self.client.post(
            "/webhook/tpbank/uat",
            json=payload,
            headers={"Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/tpbank/uat [1tx]",
        ) as resp:
            body = resp.json()
            if body.get("code") == "401":
                resp.failure("Signature rejected")
            else:
                resp.success()

    @task(1)
    def get_health(self):
        """Health check — baseline latency."""
        self.client.get("/health", name="GET /health")

    @task(1)
    def get_metrics(self):
        """Prometheus metrics endpoint."""
        self.client.get("/metrics", name="GET /metrics")


class StressUser(HttpUser):
    """Stress test — heavy batch + high concurrency."""
    wait_time = between(0.05, 0.2)   # ~5–20 req/s per user

    @task(5)
    def post_large_batch(self):
        """POST 10 transactions — heavy payload."""
        payload = _make_payload(num_transactions=10)
        with self.client.post(
            "/webhook/tpbank/notification",
            json=payload,
            headers={"Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/tpbank/notification [10tx]",
        ) as resp:
            body = resp.json()
            if body.get("code") == "401":
                resp.failure("Signature rejected")
            else:
                resp.success()

    @task(2)
    def post_single_fast(self):
        """Single tx, high frequency."""
        payload = _make_payload(num_transactions=1)
        with self.client.post(
            "/webhook/tpbank/notification",
            json=payload,
            headers={"Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/tpbank/notification [stress-1tx]",
        ) as resp:
            body = resp.json()
            if body.get("code") == "401":
                resp.failure("Signature rejected")
            else:
                resp.success()


# ── Event hooks ───────────────────────────────────────────────────────────────

@events.init.add_listener
def on_locust_init(environment, **kwargs):
    if isinstance(environment.runner, MasterRunner):
        return
    print("\n" + "="*60)
    print("TPBank Webhook Load Test")
    print("="*60)
    print(f"  Private key : {PRIVATE_KEY_PATH}")
    print(f"  Public key  : {PUBLIC_KEY_PATH}")
    print()
    print("  ⚠️  QUAN TRỌNG: Trước khi test, cần swap public key:")
    print(f"     scp {PUBLIC_KEY_PATH} 192.168.255.71:/opt/tpb-webhook/certs/bank_public.pem")
    print("     ssh 192.168.255.71 'cd /opt/tpb-webhook && docker compose restart webhook-api'")
    print()
    print("  Sau khi test xong, restore key gốc:")
    print("     ssh 192.168.255.71 'cd /opt/tpb-webhook && git checkout certs/bank_public.pem && docker compose restart webhook-api'")
    print("="*60 + "\n")


# ── CLI setup helper ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--setup" in sys.argv:
        print(f"Public key saved to: {PUBLIC_KEY_PATH}")
        print(f"\nCopy to server:")
        print(f"  scp {PUBLIC_KEY_PATH} 192.168.255.71:/opt/tpb-webhook/certs/bank_public.pem")
        print(f"\nRestart API:")
        print(f"  ssh 192.168.255.71 'cd /opt/tpb-webhook && docker compose restart webhook-api'")
