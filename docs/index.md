---
layout: home

hero:
  name: MiAir Next
  text: 小爱音箱全能音频渲染器
  tagline: 化身 DLNA 渲染器、AirPlay 接收器与 Spotify Connect 设备，附带现代化 Web 管理后台，一条命令即可部署。
  actions:
    - theme: brand
      text: 快速开始 →
      link: /quickstart
    - theme: alt
      text: 查看源码
      link: https://github.com/deerwan/miair-next
    - theme: alt
      text: Docker Hub
      link: https://hub.docker.com/r/mrdeer1997/miair-next

features:
  - icon: 🎵
    title: DLNA 渲染器
    details: 把小爱音箱注册为标准 UPnP AV 媒体渲染器，网易云音乐、QQ 音乐等 App 一键投送。
    link: /features
    linkText: 了解更多
  - icon: 🍎
    title: AirPlay 接收器
    details: 支持 iPhone / Mac 音频投送；标准镜像内置实验性 AirPlay 2，PTP 时序桥接与输出延迟校准。
  - icon: 🎧
    title: Spotify Connect
    details: 内置官方 Soloist 引擎，作为标准 Spotify Connect 设备被发现，无损拉流与曲目信息展示。
  - icon: ☁️
    title: 小米云控制
    details: 扫码登录小米账号，远程管理设备、控制播放与音量，触屏歌词与封面异步匹配小米曲库。
  - icon: 🖥️
    title: Web 管理后台
    details: Vue 3 + Naive UI 现代后台：实时状态推送、三态主题、通知推送、热重启与日志管理。
  - icon: 📦
    title: 一条命令部署
    details: 多架构 Docker 镜像（amd64 / arm64），另提供 fnOS 应用与 iStore / LuCI 插件包。
---

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/deerwan/miair-next/main/install.sh | bash
```

::: warning 部署前提
DLNA / AirPlay 依赖组播发现，**必须使用 host 网络**，仅支持 **Linux 宿主机**（macOS / Windows 的 Docker Desktop 不支持 host 网络组播）。详见[部署与配置](/deployment)。
:::

## 管理后台预览

![MiAir Next 管理后台](/preview.png)

## Star 趋势

如果这个项目对您有所帮助，欢迎点亮一个 Star ⭐，感谢您的支持！

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=deerwan%2Fmiair-next&amp;type=Date&amp;theme=dark" />
  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=deerwan%2Fmiair-next&amp;type=Date" />
  <img alt="Star-History" src="https://api.star-history.com/svg?repos=deerwan%2Fmiair-next&amp;type=Date" />
</picture>
