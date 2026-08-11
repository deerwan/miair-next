#!/bin/sh
# Author: deer

ACTION=${1}
shift 1

istoreenhance_pull() {
  local image_name="$1"
  echo "docker pull ${image_name}"
  docker pull "$image_name"
  if [ $? -ne 0 ]; then
    local isInstall=$(command -v iStoreEnhance)
    local isRun=$(pgrep iStoreEnhance)
    if [ -n "$isRun" ]; then
      local registry_mirror=$(docker info 2>/dev/null | awk -F': ' '/Registry Mirrors:/ {found=1; next} found && NF {if ($0 ~ /registry.linkease.net/) {print; exit}}')
      if [[ -n "$registry_mirror" ]]; then
        echo "istoreenhance_pull failed"
      else
        echo "download failed, not found registry.linkease.net"
      fi
    else
      if [ -z "$isInstall" ]; then
        echo "download failed, install istoreenhance to speedup, \"https://doc.linkease.com/zh/guide/istore/software/istoreenhance.html\""
      else
        echo "download failed, enable istoreenhance to speedup"
      fi
    fi
    exit 1
  fi
}

do_install() {
  local port=`uci get miair-next.@main[0].port 2>/dev/null`
  local config_dir=`uci get miair-next.@main[0].config_dir 2>/dev/null`
  local image_name=`uci get miair-next.@main[0].image_name 2>/dev/null`

  [ -z "$image_name" ] && image_name="mrdeer1997/miair-next:latest"
  [ -z "$port" ] && port=8300
  [ -z "$config_dir" ] && config_dir="/root/Configs/MiAirNext"

  if [ -z "$config_dir" ]; then
      echo "config path is empty!"
      exit 1
  fi

  istoreenhance_pull "$image_name"
  docker rm -f miair-next

  mkdir -p "$config_dir"

  local cmd="docker run --restart=unless-stopped -d --name miair-next --network host --dns=127.0.0.1"

  local tz="`uci get system.@system[0].zonename | sed 's/ /_/g'`"
  [ -z "$tz" ] || cmd="$cmd -e TZ=$tz"

  cmd="$cmd \
    -e MIAIR_WEB_PORT=$port \
    -e MIAIR_GITHUB_REPO=deerwan/miair-next \
    -v \"$config_dir:/app/data\" \
    \"$image_name\""

  echo "$cmd"
  eval "$cmd"
}

usage() {
  echo "usage: $0 sub-command"
  echo "where sub-command is one of:"
  echo "      install                Install the MiAir Next"
  echo "      upgrade                Upgrade the MiAir Next"
  echo "      rm/start/stop/restart  Remove/Start/Stop/Restart the MiAir Next"
  echo "      status                 MiAir Next status"
  echo "      port                   MiAir Next port"
}

case ${ACTION} in
  "install")
    do_install
  ;;
  "upgrade")
    do_install
  ;;
  "rm")
    docker rm -f miair-next
  ;;
  "start" | "stop" | "restart")
    docker ${ACTION} miair-next
  ;;
  "status")
    docker ps --all -f 'name=^/miair-next$' --format '{{.State}}'
  ;;
  "port")
    uci get miair-next.@main[0].port 2>/dev/null || echo "8300"
  ;;
  *)
    usage
    exit 1
  ;;
esac
