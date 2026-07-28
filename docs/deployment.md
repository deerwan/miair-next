# 部署与配置

## 前提:必须使用 host 网络

DLNA 依赖 SSDP(1900/udp)、AirPlay 依赖 mDNS(5353/udp)组播,桥接网络下投送端无法发现音箱。因此仅支持 **Linux 宿主机**(macOS / Windows 的 Docker Desktop 不支持 host 网络组播)。

host 网络下容器直接绑定宿主机端口,**不要**写 `-p 8300:8300`,改端口只需调整环境变量 `MIAIR_WEB_PORT`。

## 安装方式

### 方式一:一键安装脚本

```bash
curl -fsSL https://raw.githubusercontent.com/deerwan/miair-next/main/install.sh | bash
```

### 方式二:docker run

```bash
docker run -d \
  --name miair-next \
  --network host \
  --restart unless-stopped \
  -e MIAIR_WEB_PORT=8300 \
  -v $(pwd)/data:/app/data \
  mrdeer1997/miair-next:latest
```

### 方式三:docker compose

```bash
docker compose up -d
```

启动后访问 `http://<宿主机IP>:8300`,首次访问将引导创建管理员账号,登录后在「账号配置」中填入小米账号 / Cookie 并选择音箱。

## 管理脚本 manage.sh

```bash
./manage.sh start      # 启动容器
./manage.sh stop       # 停止容器
./manage.sh restart    # 重启容器
./manage.sh logs       # 查看最近日志 (logs -f 实时跟踪)
./manage.sh status     # 查看容器状态
./manage.sh update     # 拉取最新镜像并重建容器 (数据保留)
./manage.sh uninstall  # 删除容器 (数据目录保留)
```

可用环境变量覆盖默认约定:`MIAIR_IMAGE`(镜像)、`MIAIR_CONTAINER`(容器名)、`MIAIR_DATA_DIR`(数据目录)、`MIAIR_WEB_PORT`(Web 端口)。

## 镜像标签策略

| 标签 | 触发方式 | 用途 |
| --- | --- | --- |
| `edge` | 推送到 `main` 分支 | 尝鲜 / 测试最新代码 |
| `latest` | 打 `v*.*.*` tag | 最新稳定版 |
| `1.2.3` / `1.2` | 打 `v1.2.3` tag | 锁定具体版本 |
| `<short-sha>` | 每次构建 | 精确回滚定位 |

镜像由 GitHub Actions 云端构建,支持 `linux/amd64`、`linux/arm64`(涵盖 x86 主机 / NAS、树莓派 4+ 等主流部署环境)。打 tag 时 CI 还会自动创建 GitHub Release,供管理后台「检查更新」检测。

## 数据目录

所有运行数据持久化在数据目录(容器内 `/app/data`,宿主机映射目录):

```
data/
├── conf/          # 引擎业务配置 (config.json / .mi.token / miair.log)
├── miair.db       # SQLite (管理员账号)
└── secret.key     # JWT 密钥 (首次启动随机生成)
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MIAIR_DATA` | `/app/data` | 数据目录 |
| `MIAIR_WEB_HOST` | `0.0.0.0` | Web 监听地址 |
| `MIAIR_WEB_PORT` | `8300` | Web 管理后台端口(host 网络直连,无需 `-p` 映射) |
| `MIAIR_GITHUB_REPO` | `deerwan/miair-next` | 检查更新用的 GitHub 仓库(Releases API) |
| `MIAIR_TOKEN_EXPIRE_HOURS` | `24` | JWT 有效期(小时) |

## 公网暴露 (安全加固)

默认部署面向局域网。若需从公网访问管理后台:

- **不要直接把 8300 端口暴露到公网**。登录后的 JWT 走明文 HTTP, 存在被嗅探风险;
- 建议套一层 HTTPS 反向代理, 以 Caddy 为例 (自动签发证书):

```
miair.example.com {
    reverse_proxy 127.0.0.1:8300
}
```

  Nginx 同理, 需额外为 `/api/v1/ws` 配置 WebSocket 升级头 (`Upgrade` / `Connection`);
- 反代只需转发 Web 管理后台。DLNA/AirPlay 的组播发现仅局域网有效, 无法也不应暴露到公网;
- 配合防火墙限制 8300 仅本机/内网访问 (例如 `ufw allow from 192.168.0.0/16 to any port 8300`)。

## 升级

```bash
./manage.sh update
# 或手动: docker pull mrdeer1997/miair-next:latest && 删除旧容器后按「方式二」重建
```

数据目录挂载在宿主机,升级重建容器不会丢失配置。
