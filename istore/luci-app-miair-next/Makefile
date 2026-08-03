include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-miair-next
PKG_VERSION:=1.0.0
PKG_RELEASE:=1

define Package/$(PKG_NAME)/conffiles
/etc/config/miair-next
endef

LUCI_TITLE:=LuCI support for MiAir Next (DLNA / AirPlay renderer for Xiaomi speakers)
LUCI_PKGARCH:=all
LUCI_DEPENDS:=+lsblk +docker +luci-lib-taskd +luci-lib-docker

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
