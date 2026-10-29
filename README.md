<div align="center">
    <h1>MiAir Next</h1>
    <p><em>🌐 让小米小爱音箱化身 <strong>DLNA 渲染器</strong>、<strong>AirPlay 接收器</strong> 与 <strong>Spotify Connect 设备</strong>，并附带一个现代化的 Web 管理后台。</em></p>
    <p>
        <a href="./README.md">简体中文</a> &nbsp;·&nbsp; <a href="./README_en.md">English</a> &nbsp;·&nbsp; <a href="./docs/deployment.md">部署文档</a> &nbsp;·&nbsp; <a href="https://t.me/deerwan">Telegram</a>
    </p>
    <p align="center">
        <a href="https://github.com/deerwan/miair-next/blob/main/LICENSE"><img src="https://img.shields.io/github/license/deerwan/miair-next" alt="License" /></a>
        <a href="https://hub.docker.com/r/mrdeer1997/miair-next"><img src="https://img.shields.io/docker/pulls/mrdeer1997/miair-next" alt="Docker Pulls" /></a>
        <a href="https://github.com/deerwan/miair-next/releases"><img src="https://img.shields.io/github/release/deerwan/miair-next" alt="latest version" /></a>
        <a href="https://github.com/deerwan/miair-next/stargazers"><img src="https://img.shields.io/github/stars/deerwan/miair-next" alt="Stars" /></a>
        <a href="https://github.com/deerwan/miair-next/network/members"><img src="https://img.shields.io/github/forks/deerwan/miair-next" alt="Forks" /></a>
        <a href="https://t.me/deerwan"><img src="https://img.shields.io/badge/-Telegram-26A5E4?logo=telegram&logoColor=white" alt="Telegram" /></a>
    </p>
</div>

---

# 1. 💡 项目介绍

MiAir Next 让小米小爱音箱化身 **DLNA 渲染器**、**AirPlay 接收器** 与 **Spotify Connect 设备**，并附带一个现代化的 Web 管理后台。

本项目在 [MiAir](https://github.com/KiriChen-Wind/MiAir) 协议层的基础上重构：后端改用 **FastAPI**，前端使用 **Vue 3 + Vite + Naive UI** 自建轻量管理后台，通过 GitHub Actions 云端构建多架构 Docker 镜像，一条命令即可部署。

![preview](img/image.png)

---

# 2. ✨ 功能特性

支持将小爱音箱变为 **DLNA 渲染器**、**AirPlay 接收器** 与 **Spotify Connect 设备**，并提供小米云控制、触屏歌词/封面、Web 管理后台、通知推送、三态主题与热重启等能力。

> 完整功能清单详见 [功能特性文档](docs/features.md)。

标准镜像已内置 AirPlay 2 组件，按原有流程安装或更新后，可在服务设置中为一个音箱启用 AirPlay 2（实验性）；其他音箱继续使用 AirPlay 1。参阅[配置和限制](docs/deployment-airplay2.md)。声学同步尚未验证。

---

# 3. 🚀 快速开始（Docker，推荐）

> ⚠️ **必须使用 host 网络**：DLNA/AirPlay 依赖组播发现，仅支持 **Linux 宿主机**。

```bash
curl -fsSL https://raw.githubusercontent.com/deerwan/miair-next/main/install.sh | bash
```

> 完整部署命令、docker run 参数与首次配置步骤详见 [快速开始文档](docs/quickstart.md) 与 [部署与配置](docs/deployment.md)。

---

# 4. 📦 依赖组件

后端基于 **FastAPI**，前端为 **Vue 3 + Vite + Naive UI**，运行时依赖 `ffmpeg`、`pulseaudio` 等系统组件，以及按需下载的 **Spotify Soloist** 官方二进制。

> 完整依赖清单与用途详见 [依赖组件文档](docs/dependencies.md)。

---

# 5. 📚 文档

| 文档 | 内容 |
| --- | --- |
| [功能特性](docs/features.md) | DLNA / AirPlay / Spotify Connect / 小米云 / Web 后台等全部功能说明 |
| [快速开始](docs/quickstart.md) | Docker 一键安装、docker run 参数、首次配置步骤 |
| [依赖组件](docs/dependencies.md) | 运行时依赖与系统组件清单 |
| [部署与配置](docs/deployment.md) | 安装方式、manage.sh 管理脚本、镜像标签策略、数据目录、环境变量、升级 |
| [Spotify 畅联指南](docs/spotify.md) | Spotify Connect 配置、API Key 获取、音量控制模型、离线安装与故障排查 |
| [接口文档](docs/api.md) | REST / WebSocket 接口清单、鉴权说明、快速验证 |
| [本地开发](docs/development.md) | 前后端启动、测试、构建镜像、发版流程、项目结构 |

---

# 6. ⚖️ 许可证与第三方

## 📄 许可证

本项目以 **GPL-3.0-or-later** 发布，详见 [LICENSE](LICENSE)。

协议引擎包含 / 衍生自以下第三方代码，各组件许可：

| 组件 | 来源 | 原许可 |
| --- | --- | --- |
| `engine/airplay/fairplay3.py`、`engine/airplay/dxxp.py` | systemcrash（OmgHax） | GPL-2.0 |
| `engine/airplay/server.py`、`pairing/`、`connections/` | [airplay2-receiver](https://github.com/openairplay/airplay2-receiver)（简化改写） | Apache-2.0 |
| `engine/airplay/airplay1.py` | Airport Express 公开密钥实现 | LGPL-2.0+ |
| DLNA / 小米云引擎层 | [MiAir](https://github.com/KiriChen-Wind/MiAir)（移植） | MIT |

> 引擎同时包含 Apache-2.0 与 GPL 系代码，故整体按 GPL-3.0-or-later 分发（与同样内置 fairplay3 组件的 [Macast](https://github.com/xfangfang/Macast) 做法一致）。

---

# 7. 🙏 致谢

协议层实现移植 [MiAir](https://github.com/KiriChen-Wind/MiAir)。

部分实现参考 [songloft-plugin-miot](https://github.com/songloft-org/songloft-plugin-miot)。

扫码登录参考 [xiaomusic](https://github.com/hanxi/xiaomusic)。

AirPlay 元数据解析参考 [shairport-sync-metadata-reader](https://github.com/mikebrady/shairport-sync-metadata-reader)。

感谢 [LINUX DO](https://linux.do/) 社区。

---

# 8. 👥 贡献者

感谢所有为项目贡献代码、文档、创意和反馈的开发者！

[![贡献者](https://contrib.rocks/image?repo=deerwan/miair-next)](https://github.com/deerwan/miair-next/graphs/contributors)

---

# 9. ⭐ Star 趋势

**如果这个项目对您有所帮助，欢迎点亮一个 Star ⭐，感谢您的支持！**

<a href="https://github.com/deerwan/miair-next">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=deerwan%2Fmiair-next&amp;type=Date&amp;theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=deerwan%2Fmiair-next&amp;type=Date" />
   <img alt="Star-History" src="https://api.star-history.com/svg?repos=deerwan%2Fmiair-next&amp;type=Date" />
 </picture>
</a>
