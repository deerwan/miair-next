local sys  = require "luci.sys"
local uci  = require "luci.model.uci".cursor()
local keyword = "miair-next"

m = Map(keyword, translate("MiAir Next"),
    translate("让小米小爱音箱化身 DLNA / AirPlay 接收器，并附带现代化 Web 管理后台。"))

s = m:section(NamedSection, keyword, keyword, translate("状态"))

docker_install = s:option(DummyValue, "_docker_install", translate("Docker"))
docker_start   = s:option(DummyValue, "_docker_start", translate("Docker 服务"))
container_install = s:option(DummyValue, "_container_install", translate("容器"))
container_running = s:option(DummyValue, "_container_running", translate("运行状态"))

btn_install = s:option(Button, "_install", translate("安装 MiAir Next"))
btn_start   = s:option(Button, "_start", translate("启动"))
btn_stop    = s:option(Button, "_stop", translate("停止"))
btn_uninstall = s:option(Button, "_uninstall", translate("删除容器"))
btn_open    = s:option(Button, "_open", translate("打开 Web 后台"))

btn_install.inputstyle = "apply"
btn_start.inputstyle = "apply"
btn_stop.inputstyle = "reset"
btn_uninstall.inputstyle = "remove"
btn_open.inputstyle = "apply"

function docker_install.cfgvalue(self, section)
    return (sys.call("which docker >/dev/null") == 0) and translate("已安装") or translate("未安装")
end

function docker_start.cfgvalue(self, section)
    return (sys.call("docker info >/dev/null 2>&1") == 0) and translate("运行中") or translate("未运行")
end

function container_install.cfgvalue(self, section)
    return (sys.call("docker ps -aqf'name="..keyword.."' | grep -q .") == 0) and translate("已安装") or translate("未安装")
end

function container_running.cfgvalue(self, section)
    local id = sys.exec("docker ps -aqf'name="..keyword.."'")
    if id and #id > 0 then
        return (sys.call("docker ps -qf'name="..keyword.."' | grep -q .") == 0) and translate("运行中") or translate("已停止")
    end
    return translate("—")
end

local port = uci:get_first(keyword, keyword, "port") or "8300"

function btn_install.write(self, section)
    luci.http.redirect(luci.dispatcher.build_url("admin", "services", keyword, "install"))
end

function btn_start.write(self, section)
    luci.http.redirect(luci.dispatcher.build_url("admin", "services", keyword, "start"))
end

function btn_stop.write(self, section)
    luci.http.redirect(luci.dispatcher.build_url("admin", "services", keyword, "stop"))
end

function btn_uninstall.write(self, section)
    luci.http.redirect(luci.dispatcher.build_url("admin", "services", keyword, "uninstall"))
end

function btn_open.write(self, section)
    local host = luci.http.getenv("SERVER_ADDR") or ""
    luci.http.redirect("http://"..host..":"..port)
end

return m
