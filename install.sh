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
warn() { printf '\033[33m[MiAir Next]\033[0m %s\n' "$1" >&2; }
err()  { printf '\033[31m[MiAir Next]\033[0m %s\n' "$1" >&2; }

# ---- 探测本机局域网 IP (host 网络下与容器内一致; 可用 MIAIR_HOSTNAME 覆盖) ----
# 三级探测, 解决软路由 WAN 口公网 IP 场景探测失败问题:
#   1) ip addr 枚举真实网卡地址 (过滤 docker/tailscale/veth/ppp 等虚拟接口)
#   2) hostname -I 私有地址 (busybox 不支持 -I 时失败自动跳过)
#   3) 默认路由源 IP, 仅当其是私网段时采用 (公网 WAN IP 会被拒绝)
detect_lan_ip() {
  if [ -n "${MIAIR_HOSTNAME:-}" ]; then
    printf '%s' "${MIAIR_HOSTNAME}"
    return 0
  fi
  # 1) 优先枚举真实网卡地址, 优先 192.168.* > 10.* > 172.16-31.*
  if command -v ip >/dev/null 2>&1; then
    local ip
    ip="$(ip -o -4 addr show scope global 2>/dev/null | awk '
      $2 !~ /^(lo|docker[0-9]*|tailscale[0-9]*|veth.*|br-[a-f0-9]+|wg[0-9]*|tun[0-9]*|tap[0-9]*|ppp[0-9]*)$/ {
        split($4, a, "/"); candidate=a[1]
        if (candidate ~ /^192\.168\./) { best=candidate; exit }
        if (candidate ~ /^10\./ && best == "") best=candidate
        if (candidate ~ /^172\.(1[6-9]|2[0-9]|3[0-1])\./ && best == "") best=candidate
        if (fallback == "") fallback=candidate
      }
      END { if (best != "") print best; else if (fallback != "") print fallback }
    ')"
    [ -n "$ip" ] && printf '%s' "$ip" && return 0
  fi
  # 2) 回退: hostname -I 中的私有地址
  if command -v hostname >/dev/null 2>&1; then
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{
      for (i=1; i<=NF; i++) {
        if ($i ~ /^192\.168\./) { print $i; exit }
        if ($i ~ /^10\./ && best == "") best=$i
        if ($i ~ /^172\.(1[6-9]|2[0-9]|3[0-1])\./ && best == "") best=$i
      }
      if (best != "") print best
    }')"
    [ -n "$ip" ] && printf '%s' "$ip" && return 0
  fi
  # 3) 回退: ifconfig 私有地址 (macOS / 精简 Linux 缺少 ip 命令时)
  if command -v ifconfig >/dev/null 2>&1; then
    local ip
    ip="$(ifconfig 2>/dev/null | awk '
      /^[a-zA-Z]/ { iface=$1; sub(/:.*/, "", iface) }
      $1 == "inet" && $2 !~ /^127\./ &&
      iface !~ /^(lo|docker|veth|utun|awdl|llw|anpi|bridge|vmnet)/ {
        split($2, a, "/"); candidate=a[1]
        if (candidate ~ /^192\.168\./) { best=candidate; exit }
        if (candidate ~ /^10\./ && best == "") best=candidate
        if (candidate ~ /^172\.(1[6-9]|2[0-9]|3[0-1])\./ && best == "") best=candidate
      }
      END { if (best != "") print best }
    ')"
    [ -n "$ip" ] && printf '%s' "$ip" && return 0
  fi
  # 4) 最后兜底: 默认路由源 IP, 仅私网段采用 (软路由 WAN 公网 IP 场景会被拒绝)
  if command -v ip >/dev/null 2>&1; then
    local ip
    ip="$(ip -4 route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
    case "${ip}" in
      10.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|192.168.*)
        case "${ip}" in 172.17.*|172.18.*) ;; *)
          printf '%s' "${ip}"; return 0 ;;
        esac ;;
    esac
  fi
  return 1
}

LAN_IP="$(detect_lan_ip || true)"
if [ -n "${LAN_IP}" ]; then
  info "局域网 IP: ${LAN_IP} (若与实际不符, 可用 MIAIR_HOSTNAME=正确IP 覆盖)"
else
  warn "未探测到局域网 IP!"
  warn "音频流地址将回退为 127.0.0.1, 音箱无法拉取音频流 (AirPlay/DLNA 无声)!"
  if [ -t 0 ]; then
    read -r -p "请输入宿主机的局域网 IP 地址: " LAN_IP
    while [ -z "${LAN_IP}" ] || [ "${LAN_IP}" = "127.0.0.1" ]; do
      err "IP 地址无效, 请重新输入"
      read -r -p "请输入宿主机的局域网 IP 地址: " LAN_IP
    done
  else
    warn "非交互模式, 继续安装 (容器内将尝试自动检测)。"
    warn "如需指定, 请使用: curl -fsSL <install_url> | MIAIR_HOSTNAME=<本机局域网IP> bash"
    warn "安装后可在启动日志中查看 \"主机名\" 行验证是否正确"
  fi
fi


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
if [ -n "${LAN_IP}" ]; then
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --network host \
    --restart unless-stopped \
    -e "MIAIR_WEB_PORT=${WEB_PORT}" \
    -e "MIAIR_HOSTNAME=${LAN_IP}" \
    -v "${DATA_DIR}:/app/data" \
    "${IMAGE}"
else
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --network host \
    --restart unless-stopped \
    -e "MIAIR_WEB_PORT=${WEB_PORT}" \
    -v "${DATA_DIR}:/app/data" \
    "${IMAGE}"
fi

info "启动完成!"
echo
info "管理后台: http://<本机IP>:${WEB_PORT}"
info "首次访问将引导创建管理员账号。"
echo
info "查看日志: docker logs -f ${CONTAINER_NAME}"
info "停止服务: docker rm -f ${CONTAINER_NAME}"
