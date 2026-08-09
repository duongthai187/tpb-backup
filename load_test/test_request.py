import base64
import random
import string
import uuid
from datetime import datetime

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

with open('test_keys/test_private.pem', 'rb') as f:
    priv = serialization.load_pem_private_key(f.read(), password=None)


def sign(source_app_id, batch_id, timestamp):
    canonical = (source_app_id + batch_id + timestamp).encode()
    sig = priv.sign(canonical, padding.PKCS1v15(), hashes.SHA512())
    return base64.b64encode(sig).decode()


def make_payload(num_transactions=1):
    source_app_id = 'TPBANK_WEBHOOK'
    batch_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    txs = []
    for _ in range(num_transactions):
        amount = round(random.uniform(10000, 100000000), 0)
        tx_id = str(uuid.uuid4())
        txs.append({
            'transactionId': tx_id,
            'tranRefNo': f'REF{random.randint(100000, 999999)}',
            'accountNumber': ''.join(random.choices(string.digits, k=10)),
            'amount': amount,
            'transType': random.choice(['C', 'D']),
            'balanceAvailable': str(int(amount)),
            'notiCreatedTime': datetime.utcnow().isoformat(),
            'transTime': datetime.utcnow().isoformat(),
            'tranDesc': f'Test tx {tx_id[:8]}',
            'ofsAccountNumber': ''.join(random.choices(string.digits, k=10)),
            'ofsAccountName': 'Test User',
            'ofsBankId': 'VCB',
            'ofsBankName': 'Test Bank',
            'isVirtualTrans': 'N',
            'virtualAcc': None,
        })
    return {
        'sourceAppId': source_app_id,
        'batchId': batch_id,
        'timestamp': timestamp,
        'signature': sign(source_app_id, batch_id, timestamp),
        'data': txs,
    }


for path in ['/webhook/tpbank/notification', '/webhook/tpbank/uat']:
    payload = make_payload(1)
    r = requests.post('http://localhost:8443' + path, json=payload, timeout=30)
    print('\nPATH', path)
    print('STATUS', r.status_code)
    print('BODY', r.text)
