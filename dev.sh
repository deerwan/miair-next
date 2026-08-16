#!/usr/bin/env bash
# miair-next 本地一键开发启动脚本
# 启动后端 (FastAPI :8300) 与前端 (Vite :5173)
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="/tmp/miair-logs"
mkdir -p "$LOG_DIR"

# 选用 >=3.10 的 python（优先 python3.12，回退 python3.11 / python3.10 / python3）
PY=""
for v in python3.12 python3.11 python3.10 python3; do
  if command -v "$v" >/dev/null 2>&1; then
    ver=$("$v" -c 'import sys;print(sys.version_info>= (3,10))' 2>/dev/null)
    if [ "$ver" = "True" ]; then PY="$v"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "✗ 未找到 Python >=3.10，请先安装 python3.10+"
  exit 1
fi
echo "• 使用 Python: $($PY --version)"

# ---------- 后端 venv ----------
cd "$BACKEND"
if [ ! -d ".venv" ]; then
  echo "• 创建虚拟环境 .venv ..."
  "$PY" -m venv .venv
fi
VENV_PY="$BACKEND/.venv/bin/python"
if ! "$VENV_PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "• 安装后端依赖 ..."
  "$VENV_PY" -m pip install --upgrade pip >/dev/null
  "$VENV_PY" -m pip install -e . >/dev/null
fi

# 若已有进程占用 8300 / 5173，先提示
kill_port() {
  pid=$(lsof -ti ":$1" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "• 端口 $1 被占用 (pid $pid)，尝试释放 ..."
    kill "$pid" 2>/dev/null || true
    sleep 1
  fi
}
kill_port 8300
kill_port 5173

echo "• 启动后端 (日志: $LOG_DIR/backend.log)"
nohup "$VENV_PY" run.py > "$LOG_DIR/backend.log" 2>&1 &
echo "  backend pid: $!"

cd "$FRONTEND"
if [ ! -d "node_modules" ]; then
  echo "• 安装前端依赖 ..."
  npm install
fi
echo "• 启动前端 (日志: $LOG_DIR/frontend.log)"
nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
echo "  frontend pid: $!"

echo
echo "✓ 已启动:"
echo "  后端  : http://localhost:8300"
echo "  前端  : http://localhost:5173"
echo "  日志  : $LOG_DIR/backend.log  $LOG_DIR/frontend.log"
echo "  (Ctrl+C 不会停止后台进程，停止请用: pkill -f 'run.py'; pkill -f vite)"
