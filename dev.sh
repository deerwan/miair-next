#!/usr/bin/env bash
# miair-next 本地一键开发启动脚本
# 启动 / 停止 / 重启 后端 (FastAPI :8300) 与前端 (Vite :5173)
#
# 用法:
#   ./dev.sh              # 等同于 ./dev.sh start
#   ./dev.sh start        # 启动前后端 (自动建 venv / 装依赖 / 释放端口)
#   ./dev.sh stop         # 停止前后端
#   ./dev.sh restart      # 重启前后端
#   ./dev.sh status       # 查看前后端运行状态
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="/tmp/miair-logs"
PID_DIR="$LOG_DIR"
BACKEND_PID="$PID_DIR/backend.pid"
FRONTEND_PID="$PID_DIR/frontend.pid"
mkdir -p "$LOG_DIR"

# ---------- 选用 >=3.10 的 python ----------
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

# ---------- 解析本机真实局域网 IPv4 (供 AirPlay 绑定) ----------
# AirPlay 要求服务绑定的 IP 与 iPhone 同一网段, 否则设备可见但连接失败。
# 默认优先取 en0 (macOS 主网卡) 的真实 IP; 可通过 MIAIR_HOSTNAME 覆盖。
detect_lan_ip() {
  if [ -n "$MIAIR_HOSTNAME" ]; then
    echo "$MIAIR_HOSTNAME"; return
  fi
  local ip=""
  # macOS: 主网卡通常是 en0
  ip=$(ipconfig getifaddr en0 2>/dev/null || true)
  if [ -z "$ip" ]; then
    # Linux / 通用: 取默认路由出口 IP
    ip=$( (command -v hostname >/dev/null && hostname -I 2>/dev/null | awk '{print $1}') || true)
  fi
  echo "$ip"
}
MIAIR_HOSTNAME="${MIAIR_HOSTNAME:-$(detect_lan_ip)}"
export MIAIR_HOSTNAME
if [ -n "$MIAIR_HOSTNAME" ]; then
  echo "• AirPlay 绑定 IP: $MIAIR_HOSTNAME"
fi

# ---------- 端口占用释放 ----------
kill_port() {
  pid=$(lsof -ti ":$1" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "• 端口 $1 被占用 (pid $pid)，尝试释放 ..."
    kill "$pid" 2>/dev/null || true
    sleep 1
  fi
}

# ---------- 依赖校验 ----------
ensure_backend_deps() {
  cd "$BACKEND"
  if [ ! -d ".venv" ]; then
    echo "• 创建虚拟环境 .venv ..."
    "$PY" -m venv .venv
  fi
  VENV_PY="$BACKEND/.venv/bin/python"
  # 部分镜像 (如清华) 可能缺失 setuptools 等构建依赖, 优先用官方源安装
  if ! "$VENV_PY" -c "import fastapi, uvicorn, miservice, aiohttp" >/dev/null 2>&1; then
    echo "• 安装后端依赖 (fastapi/uvicorn/miservice/aiohttp 等) ..."
    "$VENV_PY" -m pip install --upgrade pip setuptools wheel \
      --index-url https://pypi.org/simple >/dev/null 2>&1 || true
    # --no-build-isolation: 复用已升级的 setuptools, 避免镜像缺失构建依赖导致失败
    "$VENV_PY" -m pip install --no-build-isolation \
      --index-url https://pypi.org/simple -e . 2>&1 | tail -20
  fi
}

# ---------- 进程状态判断 ----------
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

status() {
  if pid_alive "$BACKEND_PID"; then
    echo "  后端  : 运行中 (pid $(cat "$BACKEND_PID"))  http://localhost:8300"
  else
    echo "  后端  : 未运行"
  fi
  if pid_alive "$FRONTEND_PID"; then
    echo "  前端  : 运行中 (pid $(cat "$FRONTEND_PID"))  http://localhost:5173"
  else
    echo "  前端  : 未运行"
  fi
}

stop() {
  echo "• 停止服务 ..."
  if pid_alive "$BACKEND_PID"; then
    kill "$(cat "$BACKEND_PID")" 2>/dev/null || true
  fi
  if pid_alive "$FRONTEND_PID"; then
    kill "$(cat "$FRONTEND_PID")" 2>/dev/null || true
  fi
  # 兜底: 清理可能残留的 run.py / vite 进程
  pkill -f 'backend/run.py' 2>/dev/null || true
  pkill -f 'vite' 2>/dev/null || true
  rm -f "$BACKEND_PID" "$FRONTEND_PID"
  echo "✓ 已停止"
}

start() {
  echo "• 使用 Python: $($PY --version)"

  # ---------- 后端 ----------
  ensure_backend_deps
  kill_port 8300
  echo "• 启动后端 (开发模式热重载, 日志: $LOG_DIR/backend.log)"
  cd "$BACKEND"
  nohup ./.venv/bin/python run.py --reload > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$BACKEND_PID"
  echo "  backend pid: $(cat "$BACKEND_PID")"

  # ---------- 前端 ----------
  cd "$FRONTEND"
  if [ ! -d "node_modules" ]; then
    echo "• 安装前端依赖 ..."
    npm install
  fi
  kill_port 5173
  echo "• 启动前端 (日志: $LOG_DIR/frontend.log)"
  nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$FRONTEND_PID"
  echo "  frontend pid: $(cat "$FRONTEND_PID")"

  echo
  echo "✓ 已启动:"
  echo "  后端  : http://localhost:8300"
  echo "  前端  : http://localhost:5173"
  echo "  日志  : $LOG_DIR/backend.log  $LOG_DIR/frontend.log"
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 1; start ;;
  status)  status ;;
  *)
    echo "用法: $0 {start|stop|restart|status}"
    echo
    echo "  start   - 启动前后端 (默认)"
    echo "  stop    - 停止前后端"
    echo "  restart - 重启前后端"
    echo "  status  - 查看运行状态"
    exit 1
    ;;
esac
