module("luci.controller.miair-next", package.seeall)

function index()
	entry({"admin", "services", "miair-next"}, alias("admin", "services", "miair-next", "config"), _("MiAir Next"), 30).dependent = true
	entry({"admin", "services", "miair-next", "config"}, cbi("miair-next"))
end
