#!/usr/bin/env bash
#
# MiAir Next 管理脚本 (Docker 镜像模式)
#   用法: ./manage.sh [start|stop|restart|logs|status|update|uninstall]
#
# 与一键安装脚本 install.sh 共用同一套约定, 可用环境变量覆盖:
#   MIAIR_IMAGE      镜像 (默认 mrdeer1997/miair-next:latest)
#   MIAIR_CONTAINER  容器名 (默认 miair-next)
#   MIAIR_DATA_DIR   数据目录 (默认 ./miair-next-data)
#   MIAIR_WEB_PORT   Web 端口 (默认 8300)
set -euo pipefail

IMAGE="${MIAIR_IMAGE:-mrdeer1997/miair-next:latest}"
CONTAINER_NAME="${MIAIR_CONTAINER:-miair-next}"
DATA_DIR="${MIAIR_DATA_DIR:-$(pwd)/miair-next-data}"
WEB_PORT="${MIAIR_WEB_PORT:-8300}"

info() { printf '\033[32m[MiAir Next]\033[0m %s\n' "$1"; }
err()  { printf '\033[31m[错误]\033[0m %s\n' "$1" >&2; }

if ! command -v docker >/dev/null 2>&1; then
  err "未检测到 Docker, 请先安装: https://docs.docker.com/get-docker/"
  exit 1
fi

run_container() {
  # host 网络: DLNA(SSDP)/AirPlay(mDNS) 组播发现必需
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --network host \
    --restart unless-stopped \
    -e "MIAIR_WEB_PORT=${WEB_PORT}" \
    -v "${DATA_DIR}:/app/data" \
    "${IMAGE}"
}

case "${1:-}" in
  start)
    info "启动 ${CONTAINER_NAME}..."
    docker start "${CONTAINER_NAME}"
    ;;
  stop)
    info "停止 ${CONTAINER_NAME}..."
    docker stop "${CONTAINER_NAME}"
    ;;
  restart)
    info "重启 ${CONTAINER_NAME}..."
    docker restart "${CONTAINER_NAME}"
    ;;
  logs)
    if [ "${2:-}" = "-f" ]; then
      docker logs -f "${CONTAINER_NAME}"
    else
      docker logs --tail 200 "${CONTAINER_NAME}"
    fi
    ;;
  status)
    docker ps -a --filter "name=^/${CONTAINER_NAME}$"
    ;;
  update)
    # 镜像模式升级: 拉取最新镜像 -> 重建容器 (数据目录持久化, 配置不丢)
    info "拉取最新镜像 ${IMAGE}..."
    docker pull "${IMAGE}"
    info "重建容器..."
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    mkdir -p "${DATA_DIR}"
    run_container
    info "升级完成! 数据目录 ${DATA_DIR} 已保留。"
    ;;
  uninstall)
    read -r -p "确定删除容器 ${CONTAINER_NAME}? (数据目录 ${DATA_DIR} 保留) (y/N): " confirm
    if [ "${confirm}" = "y" ] || [ "${confirm}" = "Y" ]; then
      docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
      info "容器已删除。如需彻底清理, 请手动删除数据目录: ${DATA_DIR}"
    else
      info "已取消"
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|restart|logs|status|update|uninstall}"
    echo
    echo "  start     - 启动容器"
    echo "  stop      - 停止容器"
    echo "  restart   - 重启容器"
    echo "  logs      - 查看最近日志 (logs -f 实时跟踪)"
    echo "  status    - 查看容器状态"
    echo "  update    - 拉取最新镜像并重建容器 (数据保留)"
    echo "  uninstall - 删除容器 (数据目录保留)"
    exit 1
    ;;
esac
