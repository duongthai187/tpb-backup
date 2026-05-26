#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Locust Distributed Load Test — Multi-process trên Mac
# Usage:
#   ./run_distributed.sh start [workers]   # mặc định = số CPU
#   ./run_distributed.sh stop
#   ./run_distributed.sh status
# ──────────────────────────────────────────────────────────────

set -euo pipefail

LOCUST=$(which locust)
LOCUSTFILE="$(dirname "$0")/locustfile.py"
HOST="http://localhost:8443"
WEB_PORT=8089
MASTER_HOST="127.0.0.1"
PID_DIR="/tmp/locust_pids"
NUM_CPUS=$(sysctl -n hw.logicalcpu)
# Dùng tối đa N-2 cores cho worker (giữ lại cho OS + master + editor)
DEFAULT_WORKERS=$(( NUM_CPUS > 4 ? NUM_CPUS - 2 : NUM_CPUS ))

CMD=${1:-start}
WORKERS=${2:-$DEFAULT_WORKERS}

case "$CMD" in
  start)
    echo "🚀 Starting Locust distributed: 1 master + $WORKERS workers"
    echo "   Host: $HOST | Web UI: http://localhost:$WEB_PORT"
    echo ""
    mkdir -p "$PID_DIR"

    # Start master
    "$LOCUST" -f "$LOCUSTFILE" \
      --master \
      --host "$HOST" \
      --web-port "$WEB_PORT" \
      --expect-workers "$WORKERS" \
      > /tmp/locust_master.log 2>&1 &
    echo $! > "$PID_DIR/master.pid"
    echo "  ✅ Master PID $(cat $PID_DIR/master.pid) → log: /tmp/locust_master.log"

    sleep 1  # เDợi master sẵn sàng

    # Start workers
    for i in $(seq 1 "$WORKERS"); do
      "$LOCUST" -f "$LOCUSTFILE" \
        --worker \
        --master-host "$MASTER_HOST" \
        > "/tmp/locust_worker_${i}.log" 2>&1 &
      echo $! > "$PID_DIR/worker_${i}.pid"
      echo "  ✅ Worker $i PID $! → log: /tmp/locust_worker_${i}.log"
    done

    echo ""
    echo "🌐 Mở Web UI: http://localhost:$WEB_PORT"
    echo "📊 Stop:      $0 stop"
    ;;

  stop)
    echo "🛑 Stopping all Locust processes..."
    if [ -d "$PID_DIR" ]; then
      for pidfile in "$PID_DIR"/*.pid; do
        [ -f "$pidfile" ] || continue
        PID=$(cat "$pidfile")
        if kill -0 "$PID" 2>/dev/null; then
          kill "$PID" && echo "  Killed PID $PID ($(basename $pidfile .pid))"
        fi
        rm -f "$pidfile"
      done
      rmdir "$PID_DIR" 2>/dev/null || true
    else
      # Fallback: kill tất cả locust process
      pkill -f "locust" && echo "  Killed all locust processes" || echo "  No locust processes found"
    fi
    echo "✅ Done"
    ;;

  status)
    echo "📋 Locust processes:"
    pgrep -la locust || echo "  None running"
    if [ -d "$PID_DIR" ]; then
      echo ""
      echo "PID files:"
      for pidfile in "$PID_DIR"/*.pid; do
        [ -f "$pidfile" ] || continue
        PID=$(cat "$pidfile")
        NAME=$(basename "$pidfile" .pid)
        if kill -0 "$PID" 2>/dev/null; then
          echo "  ✅ $NAME PID $PID (running)"
        else
          echo "  ❌ $NAME PID $PID (dead)"
        fi
      done
    fi
    ;;

  *)
    echo "Usage: $0 {start [workers]|stop|status}"
    exit 1
    ;;
esac
