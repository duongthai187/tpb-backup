# Deploy & Cleanup Guide

## Mục lục
1. [Swap cert về gốc](#1-swap-cert-về-gốc)
2. [Xóa dữ liệu test trong DB](#2-xóa-dữ-liệu-test-trong-db)
3. [Xóa JSON files test trên server](#3-xóa-json-files-test-trên-server)
4. [Deploy code mới](#4-deploy-code-mới)
5. [Migrate DB schema](#5-migrate-db-schema-thêm-unique-index)
6. [Verify sau deploy](#6-verify-sau-deploy)

---

## Thông tin servers

| | S1 (Master) | S2 (Replica) |
|---|---|---|
| IP | `192.168.255.71` | `192.168.255.10` |
| Bastion | `103.226.250.227` | `103.226.250.227` |
| SSH key | `~/.ssh/id_rsa_gdata.pem` | `~/.ssh/id_rsa_gdata.pem` |
| Compose file | `docker-compose.yml` | `docker-compose.s2.yml` |
| Tunnel API | `localhost:8443` | `localhost:8444` |
| Tunnel Grafana | `localhost:3000` | `localhost:3001` |
| Tunnel DB | `localhost:5434` | `localhost:5433` |
| Project dir | `/opt/tpb-webhook` | `/opt/tpb-webhook` |

> **Note:** Port 5432 local bị PostgreSQL Mac chiếm → S1 DB tunnel dùng 5434.

### Mở tunnel (cả 2 server)

```bash
pkill -f "sleep 86400"; sleep 1

# S1
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no -o ConnectTimeout=30 \
  -L 8443:localhost:8443 -L 3000:localhost:3000 -L 5434:localhost:5432 \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -o ConnectTimeout=30 -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 -f -n sleep 86400

# S2
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no -o ConnectTimeout=30 \
  -L 8444:localhost:8443 -L 3001:localhost:3000 -L 5433:localhost:5432 \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -o ConnectTimeout=30 -W %h:%p root@103.226.250.227" \
  root@192.168.255.10 -f -n sleep 86400
```

---

## 1. Swap cert về gốc

> Sau khi load test, cert `bank_public.pem` và `bank_public_uat.pem` đã bị thay bằng test key. Cần restore.

```bash
# S1
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "cd /opt/tpb-webhook && \
   git checkout certs/bank_public.pem certs/bank_public_uat.pem && \
   docker compose restart webhook-api && \
   echo 'S1 cert restored'"

# S2
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.10 \
  "cd /opt/tpb-webhook && \
   git checkout certs/bank_public.pem certs/bank_public_uat.pem && \
   docker compose -f docker-compose.s2.yml restart webhook-api && \
   echo 'S2 cert restored'"
```

**Verify:**
```bash
# Phải trả về 200 healthy, không còn bị signature rejected
curl -s http://localhost:8443/health
curl -s http://localhost:8444/health
```

---

## 2. Xóa dữ liệu test trong DB

> S2 replicate từ S1 — chỉ cần truncate trên S1, S2 tự đồng bộ.

```bash
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "docker exec postgres psql -U webhook -d webhook -c \
   'TRUNCATE TABLE public.transactions RESTART IDENTITY;' && \
   echo 'DB truncated'"
```

**Verify số rows = 0:**
```bash
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "docker exec postgres psql -U webhook -d webhook -c \
   'SELECT count(*) FROM public.transactions;'"
```

---

## 3. Xóa JSON files test trên server

> Webhook API lưu raw JSON payload vào:
> - `./data/webhook_notifications/` — production endpoint
> - `./data/webhook_notifications_uat/` — UAT endpoint

```bash
# S1 — xóa hết JSON files test
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "cd /opt/tpb-webhook && \
   find data/webhook_notifications -name '*.json' -delete && \
   find data/webhook_notifications_uat -name '*.json' -delete && \
   echo 'S1 JSON files cleared'"

# S2 — xóa hết JSON files test
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.10 \
  "cd /opt/tpb-webhook && \
   find data/webhook_notifications -name '*.json' -delete && \
   find data/webhook_notifications_uat -name '*.json' -delete && \
   echo 'S2 JSON files cleared'"
```

**Verify số files = 0:**
```bash
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "find /opt/tpb-webhook/data/webhook_notifications -name '*.json' | wc -l"
```

---

## 4. Deploy code mới

### 4.1 Commit & push (local)

```bash
cd /Users/duongthai/BTMH/BTMH-TPB-Webhook/tpb-backup
git add app/banks/tpbank/models.py app/core/models.py worker/webhook_worker/db.py
git commit -m "fix: required fields per TPBank docs, dedup unique index on transaction_id"
git push
```

### 4.2 Về `docker compose down -v`

> **`-v` CHỈ xóa named volumes, KHÔNG xóa bind mounts.**

| Volume | Loại | Bị xóa bởi `-v`? |
|---|---|---|
| `redis_data` | named | ✅ Xóa (Redis stream cleared) |
| `prometheus_data`, `loki_data`, `grafana_data` | named | ✅ Xóa (metrics history reset) |
| `./data/postgres/` | **bind mount** | ❌ **KHÔNG xóa** — DB vẫn còn |
| `./data/webhook_notifications/` | **bind mount** | ❌ **KHÔNG xóa** — JSON files vẫn còn |

→ Sau `down -v`, vẫn **phải làm bước 2 (truncate DB) và bước 3 (xóa JSON)** thủ công.

### 4.3 Deploy S1

```bash
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 << 'EOF'
cd /opt/tpb-webhook
git pull
docker compose down -v
docker compose build
docker compose up -d
docker compose ps
EOF
```

### 4.4 Deploy S2

```bash
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.10 << 'EOF'
cd /opt/tpb-webhook
git pull
docker compose -f docker-compose.s2.yml down -v
docker compose -f docker-compose.s2.yml build
docker compose -f docker-compose.s2.yml up -d
docker compose -f docker-compose.s2.yml ps
EOF
```

---

## 5. Migrate DB schema (thêm UNIQUE index)

> **Không cần chạy tay.** Worker khi start sẽ tự gọi `ensure_schema()` → `CREATE UNIQUE INDEX IF NOT EXISTS` chạy tự động trên table hiện có.
>
> `IF NOT EXISTS` đảm bảo an toàn, chạy bao nhiêu lần cũng không lỗi.
>
> Nếu muốn verify index đã tồn tại:

```bash
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "docker exec postgres psql -U webhook -d webhook -c \
   '\di transactions_*'"
```

---

## 6. Verify sau deploy

```bash
# API health
curl -s http://localhost:8443/health | python3 -m json.tool
curl -s http://localhost:8444/health | python3 -m json.tool

# Worker logs S1 — kiểm tra không có error
ssh -i ~/.ssh/id_rsa_gdata.pem -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "cd /opt/tpb-webhook && docker compose logs --tail=30 webhook-worker-1"

# Grafana S1: http://localhost:3000  (admin/admin)
# Grafana S2: http://localhost:3001  (admin/admin)
```

---

## Checklist

- [ ] Cert S1 restored (`git checkout certs/`)
- [ ] Cert S2 restored (`git checkout certs/`)
- [ ] DB truncated (count = 0)
- [ ] JSON files S1 cleared
- [ ] JSON files S2 cleared
- [ ] Code pushed
- [ ] S1 rebuilt & up
- [ ] S2 rebuilt & up
- [ ] UNIQUE index tồn tại
- [ ] API health 200 cả 2 server
- [ ] Worker logs không có ERROR
