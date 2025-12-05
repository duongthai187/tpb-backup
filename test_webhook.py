#!/usr/bin/env python3
"""
Simple Test Script for Webhook API
Test đơn giản cho Webhook notify
"""

import requests
import json
import base64
import time
from datetime import datetime
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class WebhookTester:
    def __init__(self, base_url="http://localhost:8444"):
        self.base_url = base_url
        self.private_key = None
        self.setup_test_keys()
    
    def setup_test_keys(self):
        """Tạo hoặc load test keys"""
        try:
            # Thử load private key có sẵn
            with open('bank_private.pem', 'rb') as f:
                self.private_key = serialization.load_pem_private_key(f.read(), password=None)
            print("✅ Loaded existing private key")
        except FileNotFoundError:
            # Tạo key mới nếu chưa có
            print("🔑 Generating new test keys...")
            self.private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            
            # Lưu private key
            with open('bank_private.pem', 'wb') as f:
                f.write(self.private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            
            # Lưu public key vào folder certs
            # public_key = self.private_key.public_key()
            # with open('certs/bank_public.pem', 'wb') as f:
            #     f.write(public_key.public_bytes(
            #         encoding=serialization.Encoding.PEM,
            #         format=serialization.PublicFormat.SubjectPublicKeyInfo
            #     ))
            
            # print("✅ Generated new keys: bank_private.pem, certs/bank_public.pem")
    
    def create_signature(self, source_app_id, batch_id, timestamp):
        """Tạo signature theo đúng format server expect"""
        # Canonical string: sourceAppId + batchId + timestamp
        canonical_string = f"{source_app_id}{batch_id}{timestamp}"
        # print(f"🔍 DEBUG SIGNING:")
        # print(f"   sourceAppId: '{source_app_id}'")
        # print(f"   batchId: '{batch_id}'")
        # print(f"   timestamp: '{timestamp}'")
        # print(f"   canonical_string: '{canonical_string}'")
        # print(f"   canonical_length: {len(canonical_string)} chars")
        # print(canonical_string)
        # Tạo signature với SHA512withRSA
        signature_bytes = self.private_key.sign(
            canonical_string.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA512()
        )
        # print(f"   raw_signature_length: {len(signature_bytes)} bytes")
        # print(f"   raw_signature_hex: {signature_bytes.hex()[:50]}...")
        
        # Return base64 encoded
        signature_b64 = base64.b64encode(signature_bytes).decode('utf-8')
        # print(f"   base64_signature_length: {len(signature_b64)} chars")
        # print(f"   base64_signature: {signature_b64[:50]}...")
        # print()
        
        return signature_b64
    
    def test_health(self):
        """Test health endpoint"""
        print("\n🔍 Testing health endpoint...")
        try:
            response = requests.get(f"{self.base_url}/health", timeout=10, verify=False)
            print(f"✅ Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Response: {data}")
                return True
            else:
                print(f"❌ Health check failed: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Health check error: {e}")
            return False
    
    def test_webhook_simple(self):
        """Test webhook với 1 transaction đơn giản"""
        print("\n🔍 Testing webhook with simple transaction...")
        
        current_time = datetime.now()
        batch_id = f"TEST_BATCH_{current_time.strftime('%Y%m%d_%H%M%S')}"
        source_app_id = "TEST_BANK_APP"
        timestamp = str(int(current_time.timestamp()))
        
        # Tạo test payload đơn giản
        payload = {
            "sourceAppId": source_app_id,
            "batchId": batch_id,
            "timestamp": timestamp,
            "data": [
                {
                    "transactionId": f"TXN_{current_time.strftime('%Y%m%d%H%M%S')}_001",
                    "tranRefNo": f"REF_{current_time.strftime('%H%M%S')}",
                    "accountNumber": "1234567890123",
                    "amount": 500000.0,
                    "balanceAvailable": 2000000.0,
                    "transType": "C",  # Credit
                    "notiCreatedTime": current_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "transTime": current_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "transDesc": "Test credit transaction",
                    "ofsAccountNumber": "9876543210987",
                    "ofsAccountName": "NGUYEN VAN TEST",
                    "ofsBankId": "970436",
                    "ofsBankName": "VIETCOMBANK",
                    "isVirtualTrans": "",
                    "virtualAcc": ""
                }
            ]
        }
        
        # Tạo signature
        signature = self.create_signature(source_app_id, batch_id, timestamp)
        payload["signature"] = signature
        
        print(f"📦 Test Data:")
        print(f"   Batch ID: {batch_id}")
        print(f"   Transactions: {len(payload['data'])}")
        print(f"   Signature: {signature[:30]}...")
        
        # Send request
        try:
            response = requests.post(
                f"{self.base_url}/webhook/bank-notification",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
                verify=False  
            )
            
            print(f"\n📡 Response:")
            print(f"   Status Code: {response.status_code}")
            
            # Hiển thị headers quan trọng
            if 'X-Process-Time' in response.headers:
                print(f"   Process Time: {response.headers['X-Process-Time']}s")
            
            if 'X-RateLimit-Remaining' in response.headers:
                print(f"   Rate Limit Remaining: {response.headers['X-RateLimit-Remaining']}")
            
            # Hiển thị response body
            if response.headers.get('content-type', '').startswith('application/json'):
                response_data = response.json()
                print(f"   Response Body:")
                print(json.dumps(response_data, indent=4, ensure_ascii=False))
                
                # Kiểm tra kết quả
                if response_data.get('code') == '200':
                    print("✅ Webhook processed successfully!")
                    return True
                else:
                    print(f"⚠️  Webhook processed with issues: {response_data.get('message')}")
                    return False
            else:
                print(f"   Response Text: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Request failed: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False
    
    def test_webhook_multiple_transactions(self):
        """Test webhook với nhiều transactions"""
        print("\n🔍 Testing webhook with multiple transactions...")
        
        current_time = datetime.now()
        batch_id = f"MULTI_BATCH_{current_time.strftime('%Y%m%d_%H%M%S')}"
        source_app_id = "MULTI_TEST_APP"
        timestamp = str(int(current_time.timestamp()))
        
        # Tạo payload với 3 transactions
        payload = {
            "sourceAppId": source_app_id,
            "batchId": batch_id,
            "timestamp": timestamp,
            "data": [
                {
                    "transactionId": f"TXN_{current_time.strftime('%Y%m%d%H%M%S')}_001",
                    "tranRefNo": f"REF_{current_time.strftime('%H%M%S')}_1",
                    "accountNumber": "1111111111111",
                    "amount": 100000.0,
                    "balanceAvailable": 1000000.0,
                    "transType": "C",
                    "transDesc": "Test transaction 1",
                    "isVirtualTrans": "N",
                    "virtualAcc": None
                },
                {
                    "transactionId": f"TXN_{current_time.strftime('%Y%m%d%H%M%S')}_002",
                    "tranRefNo": f"REF_{current_time.strftime('%H%M%S')}_2",
                    "accountNumber": "2222222222222", 
                    "amount": 200000.0,
                    "balanceAvailable": 800000.0,
                    "transType": "D",
                    "transDesc": "Test transaction 2",
                    "isVirtualTrans": "Y",
                    "virtualAcc": "VIRTUAL_2222"
                },
                {
                    "transactionId": f"TXN_{current_time.strftime('%Y%m%d%H%M%S')}_003",
                    "tranRefNo": f"REF_{current_time.strftime('%H%M%S')}_3",
                    "accountNumber": "3333333333333",
                    "amount": 300000.0,
                    "balanceAvailable": 500000.0,
                    "transType": "C",
                    "transDesc": "Test transaction 3",
                    "isVirtualTrans": "N",
                    "virtualAcc": None
                }
            ]
        }
        
        # Tạo signature
        signature = self.create_signature(source_app_id, batch_id, timestamp)
        payload["signature"] = signature
        
        print(f"📦 Multi-Transaction Test:")
        print(f"   Batch ID: {batch_id}")
        print(f"   Transactions: {len(payload['data'])}")
        
        # Send request
        try:
            response = requests.post(
                f"{self.base_url}/webhook/bank-notification",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
                verify=False  # Bỏ verify SSL cho test local/self-signed
            )
            
            print(f"\n📡 Response: {response.status_code}")
            
            if response.headers.get('content-type', '').startswith('application/json'):
                response_data = response.json()
                print(json.dumps(response_data, indent=2, ensure_ascii=False))
                return response_data.get('code') == '200'
            
            return False
            
        except Exception as e:
            print(f"❌ Multi-transaction test failed: {e}")
            return False
    
    def test_invalid_signature(self):
        """Test với signature không hợp lệ"""
        print("\n🔍 Testing invalid signature...")
        
        payload = {
            "sourceAppId": "INVALID_TEST",
            "batchId": "INVALID_BATCH",
            "timestamp": str(int(time.time())),
            "signature": "invalid_signature_base64",
            "data": [{
                "transactionId": "INVALID_TXN",
                "tranRefNo": "INVALID_REF",
                "accountNumber": "0000000000000",
                "amount": 1000.0,
                "transType": "C",
                "isVirtualTrans": "N",
                "virtualAcc": None
            }]
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/webhook/bank-notification",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
                verify=False  # Bỏ verify SSL cho test local/self-signed
            )
            
            print(f"✅ Status: {response.status_code}")
            if response.headers.get('content-type', '').startswith('application/json'):
                response_data = response.json()
                print(f"✅ Expected error response: {response_data}")
                return response_data.get('code') == '401'  # Should be unauthorized
            
            return False
            
        except Exception as e:
            print(f"❌ Invalid signature test error: {e}")
            return False
    
    def run_all_tests(self):
        """Chạy tất cả tests"""
        print("🚀 WEBHOOK API TEST SUITE")
        print("=" * 60)
        print(f"🎯 Target API: {self.base_url}")
        
        tests = [
            ("Health Check", self.test_health),
            ("Simple Webhook", self.test_webhook_simple),
            ("Multiple Transactions", self.test_webhook_multiple_transactions),
            ("Invalid Signature", self.test_invalid_signature)
        ]
        
        results = []
        
        for test_name, test_func in tests:
            try:
                print(f"\n{'='*20} {test_name} {'='*20}")
                result = test_func()
                results.append((test_name, result))
                
                if result:
                    print(f"✅ {test_name}: PASSED")
                else:
                    print(f"❌ {test_name}: FAILED")
                    
                time.sleep(1)  # Delay giữa các tests
                
            except Exception as e:
                print(f"❌ {test_name}: ERROR - {e}")
                results.append((test_name, False))
        
        # Summary
        print(f"\n{'='*60}")
        print("📊 TEST SUMMARY:")
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"   {test_name}: {status}")
        
        print(f"\n🎯 Results: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed! Webhook API is working correctly.")
        else:
            print("⚠️  Some tests failed. Check the logs above for details.")
        
        return passed, total


def main():
    """Main function"""
    print("🔧 Webhook API Tester")
    print("Đảm bảo API đang chạy trên http://localhost:8443")
    print("-" * 50)
    
    # Khởi tạo tester
    tester = WebhookTester()
    
    # Chạy tất cả tests
    tester.run_all_tests()
    # total = tester.create_signature("BTMH", "TPB", "120000")
    # print(total)
    # print(f"\n🏁 Testing complete: {total} passed")


if __name__ == "__main__":
    main()