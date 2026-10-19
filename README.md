# MiAir Next

让小米小爱音箱化身 **DLNA 渲染器**、**AirPlay 接收器** 与 **Spotify Connect 设备**，并附带一个现代化的 Web 管理后台。

本项目在 [MiAir](https://github.com/KiriChen-Wind/MiAir) 协议层的基础上重构：后端改用 **FastAPI**，前端使用 **Vue 3 + Vite + Naive UI** 自建轻量管理后台，通过 GitHub Actions 云端构建多架构 Docker 镜像，一条命令即可部署。

![preview](img/image.png)

---

## ✨ 功能

- **DLNA (UPnP AV)**：把小爱音箱注册为可投送的媒体渲染器，支持网易云音乐、QQ 音乐等 App 投送。
- **AirPlay**：作为 AirPlay 接收器，支持 iPhone / Mac 音频投送（FairPlay / HAP 配对）。
- **Spotify 畅联 (Spotify Connect)**：
  - **官方客户端支持**：内置 Spotify 官方 Soloist 引擎，小爱音箱可作为 Spotify Connect 设备被手机、平板及桌面端 App 发现并投送播放。
  - **支持跨网音乐控制**：区别于 DLNA / AirPlay 严格受限于同一局域网组播发现，Spotify Connect 基于云端会话同步；配对后手机无论处于移动蜂窝网络（4G/5G）还是异地 Wi-Fi，只要登录同一 Spotify 账号，均可随时随地远程向音箱投送、切歌与调音。
  - **自动二进制管理**：Soloist 官方二进制在首次启用时按架构自动从 Spotify CDN 下载至数据目录，避免内置分发；支持 90 天构建到期自动拉取新版热重启，并支持 Web 后台一键检测更新。
  - **活动设备门控**：精确追踪 Connect 设备状态，仅当本机为当前活跃播放设备时才向音箱推流，避免其他端播放时误唤醒；播放转移或登出时即刻停止推流。
  - **线性无损调音**：开播初始音量遵循后台配置并将音箱硬件拉满，由 Spotify App 音量滑杆进行 1:1 软件线性调音；暂停或断开后硬件音量自动还原为默认值，防止闲置爆音。
  - **触屏歌词与封面**：自动提取曲目元数据并智能匹配小米曲库，带屏小爱音箱可同步呈现滚动歌词与高清专辑封面。
- **小米云控制**：通过小米账号 API 控制音箱播放、音量、状态查询。
- **触屏封面定制**：在「触屏显示」设置中通过下拉选择小米云封面（audioID），带屏/触屏音箱走小米云播放时显示对应封面与歌词，保存后重新投送即可生效。
- **Web 管理后台**：登录鉴权（含登录失败限速、登录日志）、总览面板（WebSocket 实时推送当前曲目与播放源）、设备管理、播放控制、账号配置、系统设置、运行日志（可下载完整日志文件）。
- **通知推送**：登录过期等事件通过飞书 / WxPusher 推送提醒。
- **主题与更新**：亮色 / 暗色 / 跟随系统三态主题（默认跟随系统），面板内检查 GitHub 最新版本。
- **热重启子服务**：保存配置后热重启 DLNA/AirPlay/Spotify 接收器，无需重启整个容器或后台进程。

---

## 🚀 快速开始（Docker，推荐）

> ⚠️ **必须使用 host 网络**：DLNA/AirPlay 依赖组播发现，仅支持 **Linux 宿主机**。详见[部署文档](docs/deployment.md)。

一键安装：

```bash
curl -fsSL https://raw.githubusercontent.com/deerwan/miair-next/main/install.sh | bash
```

或 docker run：

```bash
docker run -d \
  --name miair-next \
  --network host \
  --restart unless-stopped \
  -e MIAIR_WEB_PORT=8300 \
  -v $(pwd)/data:/app/data \
  mrdeer1997/miair-next:latest
```

启动后访问 `http://<宿主机IP>:8300`，首次访问将引导创建管理员账号，登录后在「账号配置」中填入小米账号 / Cookie 并选择音箱。

---

## 📦 依赖组件

| 组件 | 类型 | 用途说明 |
| --- | --- | --- |
| `FastAPI` / `Uvicorn` | Python 框架 | 现代异步后端 REST API 与 WebSocket 实时状态推送 |
| `Vue 3` / `Vite` / `Naive UI` | 前端技术栈 | 响应式轻量 Web 管理后台，支持亮色/暗色三态主题 |
| `ffmpeg` | 系统依赖 | 音频实时转码与流式处理（支持 WAV 与 MP3 格式输出） |
| `pulseaudio` / `pulseaudio-utils` | 系统依赖 | 提供容器内虚拟音频输出（null sink）与低延迟 PCM 音频捕获（parec） |
| `libportaudio2` | 系统依赖 | 底层跨平台音频 I/O 支持 |
| `libatomic1` | 系统依赖 | Spotify Soloist 二进制动态依赖库 |
| `dnsutils` | 系统依赖 | 网络环境与 DNS 解析探测诊断 |
| `Spotify Soloist` | 官方客户端 | Spotify Connect 官方嵌入式二进制引擎（运行时按需从官方 CDN 自动下载，不打包分发） |

---

## 📚 文档

| 文档 | 内容 |
| --- | --- |
| [部署与配置](docs/deployment.md) | 安装方式、manage.sh 管理脚本、镜像标签策略、数据目录、环境变量、升级 |
| [Spotify 畅联指南](docs/spotify.md) | Spotify Connect 配置、API Key 获取、音量控制模型、离线安装与故障排查 |
| [接口文档](docs/api.md) | REST / WebSocket 接口清单、鉴权说明、快速验证 |
| [本地开发](docs/development.md) | 前后端启动、测试、构建镜像、发版流程、项目结构 |

---

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

## 🙏 致谢

协议层实现移植 [MiAir](https://github.com/KiriChen-Wind/MiAir)。

部分实现参考 [songloft-plugin-miot](https://github.com/songloft-org/songloft-plugin-miot)。

扫码登录参考 [xiaomusic](https://github.com/hanxi/xiaomusic)。

AirPlay 元数据解析参考 [shairport-sync-metadata-reader](https://github.com/mikebrady/shairport-sync-metadata-reader)。

感谢 [LINUX DO](https://linux.do/) 社区。

