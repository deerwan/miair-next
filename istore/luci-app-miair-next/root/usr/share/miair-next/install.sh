#!/bin/sh
# MiAir Next 安装脚本：拉取镜像并以 host 网络启动容器。
# host 网络是必需的——DLNA(SSDP 1900/udp) 与 AirPlay(mDNS 5353/udp)
# 依赖局域网组播发现，桥接网络下无法被投送端发现。

IMAGE="mrdeer1997/miair-next:latest"
CONTAINER_NAME="miair-next"
WEB_PORT="8300"
DATA_DIR="/mnt/sda3/miair-next"

# 若配置中存在自定义值则读取（兼容 iStore 配置页修改）
if [ -f /etc/config/miair-next ]; then
    WEB_PORT=$(uci -q get miair-next.miair-next.port || echo "$WEB_PORT")
    DATA_DIR=$(uci -q get miair-next.miair-next.config_dir || echo "$DATA_DIR")
    IMAGE=$(uci -q get miair-next.miair-next.image || echo "$IMAGE")
fi

# 确保数据目录存在
mkdir -p "$DATA_DIR"

# 若已存在同名容器则先移除（便于重新安装/升级）
if [ -n "$(docker ps -aqf"name=$CONTAINER_NAME")" ]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
fi

docker run -d \
    --name "$CONTAINER_NAME" \
    --network host \
    --restart unless-stopped \
    -e MIAIR_WEB_PORT="$WEB_PORT" \
    -e MIAIR_GITHUB_REPO=deerwan/miair-next \
    -v "$DATA_DIR:/app/data" \
    "$IMAGE"

echo "MiAir Next 已启动，访问 http://<路由器IP>:$WEB_PORT"
