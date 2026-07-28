#!/usr/bin/env bash
#
# MiAir Next 一键安装脚本
#   curl -fsSL https://raw.githubusercontent.com/deerwan/miair-next/main/install.sh | bash
#
# 作用: 拉取官方镜像, 以 host 网络 + 数据持久化的方式启动容器。
set -euo pipefail

# ---- 可通过环境变量覆盖 ----
IMAGE="${MIAIR_IMAGE:-mrdeer1997/miair-next:latest}"
CONTAINER_NAME="${MIAIR_CONTAINER:-miair-next}"
DATA_DIR="${MIAIR_DATA_DIR:-$(pwd)/miair-next-data}"
WEB_PORT="${MIAIR_WEB_PORT:-8300}"

info() { printf '\033[32m[MiAir Next]\033[0m %s\n' "$1"; }
err()  { printf '\033[31m[错误]\033[0m %s\n' "$1" >&2; }

# ---- 检查 Docker ----
if ! command -v docker >/dev/null 2>&1; then
  err "未检测到 Docker, 请先安装: https://docs.docker.com/get-docker/"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  err "Docker 守护进程未运行或当前用户无权限 (可尝试 sudo)"
  exit 1
fi

info "镜像: ${IMAGE}"
info "数据目录: ${DATA_DIR}"
info "Web 端口: ${WEB_PORT}"

mkdir -p "${DATA_DIR}"

info "拉取最新镜像..."
docker pull "${IMAGE}"

# ---- 移除同名旧容器 ----
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  info "移除已存在的旧容器 ${CONTAINER_NAME}..."
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

# ---- 启动 (host 网络: DLNA/AirPlay 组播发现必需) ----
info "启动容器..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  --network host \
  --restart unless-stopped \
  -e "MIAIR_WEB_PORT=${WEB_PORT}" \
  -v "${DATA_DIR}:/app/data" \
  "${IMAGE}"

info "启动完成!"
echo
info "管理后台: http://<本机IP>:${WEB_PORT}"
info "首次访问将引导创建管理员账号。"
echo
info "查看日志: docker logs -f ${CONTAINER_NAME}"
info "停止服务: docker rm -f ${CONTAINER_NAME}"
