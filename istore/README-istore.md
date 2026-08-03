# MiAir Next · iStoreOS 软件中心上架说明

让小米小爱音箱化身 **DLNA 渲染器** 与 **AirPlay 接收器**，并附带一个现代化的 Web 管理后台。
本项目以 **Docker 容器** 形态运行，通过 `luci-app-miair-next` 插件在 iStoreOS 软件中心一键安装。

---

## 功能

- **DLNA (UPnP AV)**：把小爱音箱注册为可投送的媒体渲染器，支持网易云音乐、QQ 音乐等 App 投送。
- **AirPlay**：作为 AirPlay 接收器，支持 iPhone / Mac 音频投送（FairPlay / HAP 配对）。
- **小米云控制**：通过小米账号 API 控制音箱播放、音量、状态查询。
- **Web 管理后台**：登录鉴权、总览面板、设备管理、播放控制、账号配置、系统设置、运行日志。
- **主题与更新**：亮色 / 暗色 / 跟随系统三态主题，面板内检查 GitHub 最新版本。

---

## 在 iStoreOS 上安装

1. iStoreOS 软件中心搜索 **MiAir Next** → 点击安装（会自动安装 `luci-app-miair-next` 与 Docker 依赖）。
2. 安装完成后，进入「服务 → MiAir Next」：
   - 首次点击 **安装 MiAir Next** 会拉取镜像并以 `host` 网络启动容器。
   - 启动后点击 **打开 Web 后台**，或浏览器访问 `http://<路由器IP>:8300`。
3. 首次访问会引导创建管理员账号；登录后在「账号配置」中填入小米账号 / Cookie 并选择音箱。

> ⚠️ **必须使用 host 网络**：DLNA/AirPlay 依赖局域网组播发现（SSDP 1900/udp、mDNS 5353/udp），
> 桥接网络下无法被投送端发现。路由器即宿主机，因此仅支持在 iStoreOS 路由器本机上运行。

---

## 配置项（软件中心 → 脚本 / `/etc/config/miair-next`）

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `port` | `8300` | Web 管理后台端口（host 网络下直接监听宿主机） |
| `config_dir` | `/mnt/sda3/miair-next` | 数据持久化目录（管理员账号 / JWT 密钥 / 音箱配置） |
| `image` | `mrdeer1997/miair-next:latest` | 镜像地址 |

修改后，回到状态页点击 **安装 MiAir Next** 重新拉起容器即可生效。

---

## 仓库与镜像

- 源码：<https://github.com/deerwan/miair-next>
- Docker 镜像：`mrdeer1997/miair-next:latest`（多架构：linux/amd64、linux/arm64）
