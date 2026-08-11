include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-miair-next
PKG_VERSION:=1.0.2
PKG_RELEASE:=r20260811

define Package/$(PKG_NAME)/conffiles
/etc/config/miair-next
endef

define Package/$(PKG_NAME)/postinst
#!/bin/sh
chmod +x /usr/libexec/istorec/miair-next.sh
exit 0
endef

define Package/$(PKG_NAME)/prerm
#!/bin/sh
/usr/libexec/istorec/miair-next.sh stop
exit 0
endef

LUCI_TITLE:=LuCI support for MiAir Next (DLNA / AirPlay renderer for Xiaomi speakers)
LUCI_PKGARCH:=all
LUCI_DEPENDS:=+lsblk +docker +luci-lib-taskd +luci-lib-docker

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
