# This is free software, licensed under the Apache License, Version 2.0 .

include $(TOPDIR)/rules.mk

PKG_VERSION:=1.0.0
PKG_RELEASE:=1
META_TITLE:=MiAir Next
META_TITLE.en:=MiAir Next
META_DEPENDS:=+luci-app-miair-next +luci-i18n-miair-next-zh-cn +docker-deps
META_DESCRIPTION:=让小米小爱音箱化身 DLNA / AirPlay 接收器，并附带现代化 Web 管理后台。
META_DESCRIPTION.en:=Turn Xiaomi AI speakers into DLNA / AirPlay renderers, with a modern web admin panel.
META_AUTHOR:=deerwan
META_TAGS:=net multimedia
META_ARCH:=x86_64 aarch64
META_UCI:=miair-next
META_LUCI_ENTRY:=/cgi-bin/luci/admin/services/miair-next
META_WEBSITE:=https://github.com/deerwan/miair-next
META_TUTORIAL:=https://github.com/deerwan/miair-next/blob/main/istore/README-istore.md

include ../../meta.mk

# call BuildPackage - OpenWrt buildroot signature
