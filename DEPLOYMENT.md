# Hướng dẫn triển khai TPBank Webhook Hub lên Server

## Yêu cầu server

| Thành phần | Tối thiểu |
|---|---|
| OS | Ubuntu 22.04 LTS (hoặc bất kỳ Linux x86_64) |
| RAM | 2 GB |
| Disk | 20 GB (data + logs) |
| Docker | 24.x trở lên |
| Docker Compose | v2.x trở lên (tích hợp sẵn trong Docker Desktop / Docker Engine mới) |

---

## 1. Cài Docker trên server (nếu chưa có)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Đăng xuất rồi đăng nhập lại để áp dụng group
```

Kiểm tra:
```bash
docker --version
docker compose version
```

---

## 2. Đưa code lên server

### Cách A — Git (khuyến nghị)

```bash
git clone <your-repo-url> /opt/tpb-webhook
cd /opt/tpb-webhook/tpb-backup
```

### Cách B — Copy thủ công

```bash
# Trên máy local
scp -r ./tpb-backup user@SERVER_IP:/opt/tpb-webhook/

# Trên server
cd /opt/tpb-webhook/tpb-backup
```

---

## 3. Tạo file cấu hình `.env`

```bash
cd /opt/tpb-webhook/tpb-backup
cp .env.example .env   # nếu có, hoặc tạo mới
nano .env
```

Nội dung `.env` tối thiểu:

```env
# ── Postgres ─────────────────────────────────────────────────────────
POSTGRES_USER=webhook
POSTGRES_PASSWORD=THAY_BANG_MAT_KHAU_MANH
POSTGRES_DB=webhook

# ── Grafana ──────────────────────────────────────────────────────────
# Đổi ngay sau khi deploy lần đầu
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=THAY_BANG_MAT_KHAU_MANH

# ── Redis (nếu muốn đặt password) ────────────────────────────────────
# REDIS_PASSWORD=...

# ── TPBank public key path (mặc định certs/tpbank_public.pem) ────────
# TPBANK_PUBLIC_KEY_FILE=certs/tpbank_public.pem
```

> ⚠️ File `.env` chứa mật khẩu — **không commit lên git**.

---

## 4. Chuẩn bị certificates

Copy public key của TPBank vào đúng vị trí:

```bash
# Đặt public key TPBank production
cp /path/to/tpbank_public.pem certs/tpbank_public.pem

# Đặt SSL cert cho server (nếu có)
cp /path/to/server.crt certs/server.crt
cp /path/to/server.key certs/server.key
```

---

## 5. Tạo thư mục data

```bash
mkdir -p data/postgres
mkdir -p data/webhook_notifications
mkdir -p data/webhook_notifications_uat
mkdir -p logs
```

---

## 6. Build và khởi động stack

```bash
# Build images (lần đầu hoặc sau khi sửa code)
docker compose build

# Khởi động toàn bộ stack ở background
docker compose up -d

# Xem logs realtime
docker compose logs -f
```

Chờ khoảng 30-60 giây để tất cả services healthy, rồi kiểm tra:

```bash
docker compose ps
```

Output mong đợi — tất cả STATUS là `running (healthy)` hoặc `running`:

```
NAME                    STATUS
tpb_postgres            running (healthy)
tpb_redis               running (healthy)
tpb_webhook_api         running
tpb_webhook_worker_1    running
tpb_webhook_worker_2    running
tpb_webhook_worker_3    running
tpb_prometheus          running
tpb_loki                running
tpb_promtail            running
tpb_grafana             running
```

---

## 7. Kiểm tra hoạt động

```bash
# Health check API
curl http://localhost:8443/health

# Metrics
curl http://localhost:8443/metrics
```

---

## 8. Cấu hình Grafana

1. Mở trình duyệt: `http://SERVER_IP:3000`
2. Đăng nhập với `admin` / mật khẩu đã đặt trong `.env`
3. Datasources đã được tự động provision (Prometheus + Loki)

---

## 9. Cập nhật code (deploy lại)

```bash
cd /opt/tpb-webhook/tpb-backup

# Lấy code mới (nếu dùng git)
git pull

# Rebuild và restart — downtime ~5-10 giây
docker compose build
docker compose up -d

# Hoặc chỉ restart service cụ thể
docker compose up -d --no-deps --build webhook-api
docker compose up -d --no-deps --build webhook-worker-1 webhook-worker-2 webhook-worker-3
```

---

## 10. Các lệnh vận hành thường dùng

```bash
# Xem log một service cụ thể
docker compose logs -f webhook-api
docker compose logs -f webhook-worker-1

# Xem 100 dòng cuối
docker compose logs --tail=100 webhook-api

# Restart một service
docker compose restart webhook-api

# Dừng toàn bộ stack (KHÔNG xóa data)
docker compose down

# Dừng và xóa toàn bộ container + named volumes (data bind mount vẫn an toàn)
docker compose down -v

# Xem dung lượng data
du -sh data/postgres data/webhook_notifications data/webhook_notifications_uat

# Kết nối trực tiếp vào Postgres
docker exec -it tpb_postgres psql -U webhook -d webhook

# Kết nối Redis (db=0 cache, db=1 stream)
docker exec -it tpb_redis redis-cli -n 0   # cache
docker exec -it tpb_redis redis-cli -n 1   # stream
docker exec -it tpb_redis redis-cli -n 1 XLEN webhook:batches  # số message trong stream
```

---

## 11. Cấu trúc data trên server

```
tpb-backup/
├── data/
│   ├── postgres/                  ← Toàn bộ database Postgres
│   ├── webhook_notifications/     ← JSON audit files (production)
│   │   └── YYYYMMDD/
│   │       └── 20260526_...json
│   └── webhook_notifications_uat/ ← JSON audit files (UAT)
├── logs/                          ← App logs (bind mount)
└── certs/                         ← SSL + public keys
```

> **Backup**: chỉ cần backup thư mục `data/` là đủ để restore toàn bộ.

---

## 12. Mở port firewall (nếu cần)

```bash
# Chỉ mở port cần thiết ra ngoài
sudo ufw allow 8443/tcp   # Webhook API (TPBank gọi vào)
sudo ufw allow 3000/tcp   # Grafana (nội bộ ops)

# Các port sau KHÔNG nên mở ra internet
# 5432 (Postgres), 6379 (Redis), 9090 (Prometheus), 3100 (Loki)
```
