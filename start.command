#!/bin/sh
cd "$(dirname "$0")/eval" || exit 1

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo ""
  echo "  没有找到 Python。请先安装 Python 3.8+：https://www.python.org/downloads/"
  echo ""
  read -r _
  exit 1
fi

PY=$(command -v python3 || command -v python)
echo ""
echo "  正在启动本地控制台，浏览器会自动打开…"
echo "  按 Ctrl+C 停止服务。"
echo ""
exec "$PY" serve.py
