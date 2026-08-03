local taskd = require "luci.model.tasks"
local docker = require "luci.docker"
local miair_next_model = require "luci.model.miair-next"
local m, s, o

m = taskd.docker_map("miair-next", "miair-next", "/usr/libexec/istorec/miair-next.sh",
	translate("MiAir Next"),
	translate("让小米小爱音箱化身 DLNA / AirPlay 接收器，并附带现代化 Web 管理后台。")
		.. translate("官方网站：") .. ' <a href="https://github.com/deerwan/miair-next" target="_blank">https://github.com/deerwan/miair-next</a>')

local dk = docker.new({socket_path="/var/run/docker.sock"})
local dockerd_running = dk:_ping().code == 200
local docker_info = dockerd_running and dk:info().body or {}
local docker_aspace = 0
if docker_info.DockerRootDir then
	local statvfs = nixio.fs.statvfs(docker_info.DockerRootDir)
	docker_aspace = statvfs and (statvfs.bavail * statvfs.bsize) or 0
end

s = m:section(SimpleSection, translate("Service Status"), translate("MiAir Next status:"))
s:append(Template("miair-next/status"))

s = m:section(TypedSection, "main", translate("Setup"),
		(docker_aspace < 2147483648 and
		(translate("The free space of Docker is less than 2GB, which may cause the installation to fail.")
		.. "<br>") or "") .. translate("The following parameters will only take effect during installation or upgrade:"))
s.addremove=false
s.anonymous=true

o = s:option(Value, "port", translate("Web Port").."<b>*</b>")
o.default = "8300"
o.datatype = "port"
o:description(translate("MiAir Next Web management interface port. The container uses host network for AirPlay/DLNA discovery."))

local blocks = miair_next_model.blocks()
local home = miair_next_model.home()

o = s:option(Value, "config_dir", translate("Config path").."<b>*</b>")
o.rmempty = false
o.datatype = "string"

local paths, default_path = miair_next_model.find_paths(blocks, home, "Configs")
for _, val in pairs(paths) do
  o:value(val, val)
end
o.default = default_path

o = s:option(Value, "image_name", translate("Image").."<b>*</b>")
o.rmempty = false
o.datatype = "string"
o.default = "mrdeer1997/miair-next:latest"
o:value("mrdeer1997/miair-next:latest", "mrdeer1997/miair-next:latest")
o:value("ghcr.io/deerwan/miair-next:latest", "ghcr.io/deerwan/miair-next:latest")

return m
