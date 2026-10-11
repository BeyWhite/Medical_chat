#!/usr/bin/env bash
# 启动服务（macOS / Linux）。纯 Python 3 标准库，无需安装任何依赖。
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    # 必须是 Python 3
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "[ERROR] 没有找到 Python 3。请先安装 Python 3.8 或更高版本。"
  echo "        macOS:  brew install python3"
  echo "        Ubuntu: sudo apt install python3"
  exit 1
fi

echo "============================================================"
echo "  Medical QA 5.0  -  Local Server"
echo "============================================================"
echo "  Python : $($PY -V 2>&1)"
echo "  启动后会打印访问地址（默认 http://127.0.0.1:8001）"
echo "  按 Ctrl+C 停止"
echo "============================================================"
echo

exec "$PY" -u server.py
