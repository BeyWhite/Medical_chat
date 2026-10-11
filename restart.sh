#!/usr/bin/env bash
# 重启服务（macOS / Linux）
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "  Medical QA 5.0  -  Restart Server"
echo "============================================================"
echo
echo "[1/2] 先停止 ..."
bash ./stop.sh
echo
echo "[2/2] 再启动 ..."
exec bash ./start.sh
