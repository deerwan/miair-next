module("luci.controller.miair-next", package.seeall)

function index()

	entry({'admin', 'services', 'miair-next'}, alias('admin', 'services', 'miair-next', 'client'), _('MiAir Next'), 10).dependent = true -- 首页
	entry({"admin", "services", "miair-next",'client'}, cbi("miair-next/status", {hideresetbtn=true, hidesavebtn=true}), _('MiAir Next'), 20).leaf = true
    entry({'admin', 'services', 'miair-next', 'script'}, form('miair-next/script'), _('Script'), 20).leaf = true -- 直接配置脚本

	entry({"admin", "services", "miair-next","status"}, call("container_status"))
	entry({"admin", "services", "miair-next","stop"}, call("stop_container"))
	entry({"admin", "services", "miair-next","start"}, call("start_container"))
	entry({"admin", "services", "miair-next","install"}, call("install_container"))
	entry({"admin", "services", "miair-next","uninstall"}, call("uninstall_container"))
end

local sys  = require "luci.sys"
local uci  = require "luci.model.uci".cursor()
local keyword  = "miair-next"
local util  = require("luci.util")

function container_status()
	local docker_path = util.exec("which docker")
	local docker_server_version = util.exec("docker info | grep 'Server Version'")
	local docker_install = (string.len(docker_path) > 0)
	local docker_start = (string.len(docker_server_version) > 0)
	local port = tonumber(uci:get_first(keyword, keyword, "port"))
	local config_dir = uci:get_first(keyword, keyword, "config_dir")
	local container_id = util.trim(util.exec("docker ps -aqf'name="..keyword.."'"))
	local container_install = (string.len(container_id) > 0)
	local container_running = (sys.call("pidof '"..keyword.."' >/dev/null") == 0)

	local status = {
		docker_install = docker_install,
		docker_start = docker_start,
		container_id = container_id,
		container_install = container_install,
		container_running = container_running,
		container_port = (port or 8300),
		container_config_dir = (config_dir or "/mnt/sda3/miair-next"),
	}

	luci.http.prepare_content("application/json")
	luci.http.write_json(status)
	return status
end

function stop_container()
	local status = container_status()
	local container_id = status.container_id
	util.exec("docker stop '"..container_id.."'")
end

function start_container()
	local status = container_status()
	local container_id = status.container_id
	util.exec("docker start '"..container_id.."'")
end

function install_container()
	luci.sys.call('sh /usr/share/miair-next/install.sh')
	container_status()
end

function uninstall_container()
	local status = container_status()
	local container_id = status.container_id
	util.exec("docker container rm '"..container_id.."'")
end

-- 总结：
-- docker是否安装
-- 容器是否安装
-- 获取容器id docker ps -aqf'name=miair-next'
-- 启动容器 docker start <id>
-- 停止容器 docker stop <id>
