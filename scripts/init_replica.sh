#!/usr/bin/env bash
# =============================================================================
# init_replica.sh — Bootstrap PostgreSQL streaming replica trên S2
# Chạy 1 lần trên S2 (192.168.255.10) sau khi S1 đã có replication enabled
# =============================================================================
set -euo pipefail

S1_HOST="192.168.255.71"
S1_PORT="5432"
REPL_USER="replicator"
REPL_PASS="replicator_pass"
DATA_DIR="/opt/tpb-webhook/data/postgres"
COMPOSE_FILE="docker-compose.s2.yml"

echo "==> [1/5] Stop postgres container trên S2..."
docker compose -f "$COMPOSE_FILE" stop postgres || true
docker compose -f "$COMPOSE_FILE" rm -f postgres || true

echo "==> [2/5] Xóa data dir cũ: $DATA_DIR"
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

echo "==> [3/5] pg_basebackup từ S1 ($S1_HOST:$S1_PORT)..."
docker run --rm \
  -e PGPASSWORD="$REPL_PASS" \
  -v "$DATA_DIR":/var/lib/postgresql/data \
  postgres:16-alpine \
  pg_basebackup \
    -h "$S1_HOST" \
    -p "$S1_PORT" \
    -U "$REPL_USER" \
    -D /var/lib/postgresql/data \
    -P \
    -R \
    --wal-method=stream

echo "==> [4/5] Kiểm tra standby.signal..."
if [ -f "$DATA_DIR/standby.signal" ]; then
  echo "    standby.signal OK"
else
  echo "    standby.signal không tồn tại, tạo thủ công..."
  touch "$DATA_DIR/standby.signal"
fi

echo "==> [5/5] Start postgres S2..."
docker compose -f "$COMPOSE_FILE" up -d postgres

echo ""
echo "==> Done! Đợi vài giây rồi kiểm tra replication:"
echo "    docker compose -f $COMPOSE_FILE exec postgres psql -U webhook -c \"SELECT status, received_lsn FROM pg_stat_wal_receiver;\""
