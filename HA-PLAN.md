# HA-PLAN.md
# Kế hoạch High Availability — TPBank Webhook Hub

## Kiến trúc tổng quan

```
                        ┌─────────────────────────────────────┐
TPBank ──────► APIGW ──►│  S1: 192.168.255.71  (Primary)      │
                │       │  - webhook-api :8443                 │
                │       │  - 3x workers                        │
                │       │  - Redis (local)                     │
                │       │  - PostgreSQL MASTER                 │
                │       │  - Monitoring (Prometheus/Grafana)   │
                │       └──────────────┬──────────────────────┘
                │                      │ Streaming Replication
                │       ┌──────────────▼──────────────────────┐
                └──────►│  S2: 192.168.255.10  (Standby)      │
         (healthcheck   │  - webhook-api :8443                 │
          failover)     │  - 3x workers → write → S1 PG       │
                        │  - Redis (local)                     │
                        │  - PostgreSQL REPLICA (read-only)    │
                        └─────────────────────────────────────┘
```

## Luồng dữ liệu

- **Write path:** TPBank → APIGW → webhook-api (S1 hoặc S2) → Redis local → Workers → **PostgreSQL S1 (master)**
- **Read path:** Ứng dụng đọc dữ liệu nên kết nối vào **PostgreSQL S2** (`192.168.255.10:5432`) để giảm tải S1
- **Failover app:** APIGW healthcheck `/health` — nếu S1 down thì traffic chuyển sang S2 tự động
- **DB master không failover** — S1 luôn là master; workers S2 write qua network về S1

## Cấu hình PostgreSQL Replication

| Tham số | Giá trị |
|---|---|
| Master | `192.168.255.71:5432` |
| Replica | `192.168.255.10:5432` |
| Replication user | `replicator` / `replicator_pass` |
| Replication mode | Streaming (async) |
| WAL level | `replica` |
| hot_standby | `on` (replica cho phép đọc) |

## Bootstrap replica lần đầu (chạy 1 lần trên S2)

```bash
bash scripts/init_replica.sh
```

Script sẽ:
1. Stop postgres container S2
2. Xóa data dir cũ
3. Chạy `pg_basebackup` clone toàn bộ từ S1
4. Tạo `standby.signal`
5. Start lại postgres S2

## Kiểm tra replication status

Trên S1:
```sql
SELECT client_addr, state, sent_lsn, write_lsn, replay_lsn, sync_state
FROM pg_stat_replication;
```

Trên S2:
```sql
SELECT status, received_lsn, latest_end_lsn, last_msg_receipt_time
FROM pg_stat_wal_receiver;
```

## Deploy

### S1 (đã có sẵn)
```bash
git pull
docker compose up -d --force-recreate postgres
# Đợi postgres healthy rồi restart toàn bộ nếu cần
docker compose up -d
```

### S2 (lần đầu)
```bash
git clone <repo> /opt/tpb-webhook
cd /opt/tpb-webhook
cp .env.s2.example .env  # điền credentials
bash scripts/init_replica.sh
docker compose -f docker-compose.s2.yml up -d
```

## Cấu trúc file

```
tpb-backup/
├── docker-compose.yml          # S1 — Primary stack
├── docker-compose.s2.yml       # S2 — Standby stack
├── postgres/
│   ├── pg_hba.conf             # Cho phép replication từ S2
│   └── init-replication.sql    # Tạo user replicator
└── scripts/
    └── init_replica.sh         # Bootstrap replica S2
```
