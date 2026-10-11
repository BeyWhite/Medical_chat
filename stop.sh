#!/usr/bin/env bash
# 停止服务（macOS / Linux）
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "  Stop Medical QA 5.0 Server"
echo "============================================================"

STOPPED=""

# 1) 首选：server.py 写下的 PID 文件（与端口无关）
if [ -f "data/server.pid" ]; then
  PID="$(cat data/server.pid 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "[1/3] 停止 PID=$PID ..."
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
    STOPPED=1
  fi
fi

# 2) 兜底：命令行里含 server.py 的进程
if [ -z "$STOPPED" ] && command -v pgrep >/dev/null 2>&1; then
  for p in $(pgrep -f "server\.py" 2>/dev/null); do
    echo "[2/3] 停止 PID=$p ..."
    kill "$p" 2>/dev/null || true
    sleep 1
    kill -9 "$p" 2>/dev/null || true
    STOPPED=1
  done
fi

# 3) 最后兜底：默认端口 8001
if [ -z "$STOPPED" ] && command -v lsof >/dev/null 2>&1; then
  for p in $(lsof -ti tcp:8001 2>/dev/null); do
    echo "[3/3] 停止占用 8001 端口的进程 PID=$p ..."
    kill -9 "$p" 2>/dev/null || true
    STOPPED=1
  done
fi

rm -f data/server.pid

if [ -z "$STOPPED" ]; then
  echo "[INFO] 没有找到正在运行的服务，可能已经停止了。"
else
  echo "[OK] 已停止。"
fi
