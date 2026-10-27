#!/bin/sh
set -e

DATA_DIR="${MIAIR_DATA:-/app/data}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-miair}"

# 以 root 启动时: 修正挂载数据目录属主 (宿主卷属主常为 root 或宿主用户,
# 非 root 进程无写权限会启动失败), 然后降权为 miair 运行
# (非 root 运行是安全基线; PulseAudio 本身也拒绝以 root 运行)
if [ "$(id -u)" = "0" ]; then
    chown -R miair:miair "$DATA_DIR" 2>/dev/null || true
    mkdir -p "$RUNTIME_DIR" 2>/dev/null || true
    chown miair:miair "$RUNTIME_DIR" 2>/dev/null || true
    exec gosu miair "$@"
fi

exec "$@"
