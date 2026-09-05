#!/bin/sh

# This is free software, licensed under the Apache License, Version 2.0 .

NAME=mrdeer1997/miair-next
config_load miair-next
config_get port main port
config_get image_name main image_name
config_get config_path main config_path

LOGTIME=$(date "+%Y-%m-%d %H:%M:%S")

if [ -z "$port" ]; then
    port=8300
fi

if [ -z "$image_name" ]; then
    image_name=$NAME:latest
fi

if [ -z "$config_path" ]; then
    echo "[$LOGTIME] config_path 未配置，请先在 iStore 中设置配置目录" >&2
    exit 1
fi

setup() {
    echo "[$LOGTIME] Start pulling image $image_name"
    docker pull $image_name
    echo "[$LOGTIME] Image pull completed"
}

status() {
    echo $(docker ps --format '{{.Names}}\t{{.Status}}' | grep "^miair-next\t" | awk '{print $2}')
}

start() {
    # 确保只存在一个实例
    stop
    setup
    echo "[$LOGTIME] Start creating container..."
    docker run -d \
        --name=miair-next \
        --network=host \
        --restart=unless-stopped \
        -v "$config_path":/app/data \
        $(mountpoint -q /mnt && echo "-v /mnt:/mnt:rslave" || echo "-v /mnt:/mnt") \
        -e TZ=Asia/Shanghai \
        -e MIAIR_WEB_PORT=$port \
        $image_name
    echo "[$LOGTIME] Container miair-next created"
}

stop() {
    echo "[$LOGTIME] Stopping and removing container miair-next (if exists)..."
    docker stop miair-next >/dev/null 2>&1
    docker rm -f miair-next >/dev/null 2>&1
    echo "[$LOGTIME] Container miair-next stopped and removed"
}

case "$1" in
    "status")
        status
        ;;
    "start")
        start
        ;;
    "stop")
        stop
        ;;
    "upgrade")
        stop
        start
        ;;
esac
