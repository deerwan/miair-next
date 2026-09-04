
```yaml
version: '3'

services:
  miair-next:
    # 官方镜像 (Docker Hub, CI 自动构建发布); 本地开发可改为 build: .
    image: mrdeer1997/miair-next:latest
    container_name: miair-next
    # DLNA (SSDP 1900/udp) 与 AirPlay (mDNS 5353/udp) 依赖组播,
    # 必须使用 host 网络, 否则无法被投送端发现
    network_mode: host
    restart: unless-stopped
    environment:
      # Web 管理后台端口 (host 网络下直接监听宿主机, 无需端口映射)
      - MIAIR_WEB_PORT=8300
      # 检查更新用的 GitHub 仓库 (Releases API)
      - MIAIR_GITHUB_REPO=deerwan/miair-next
      # JWT 有效期 (小时)
      - MIAIR_TOKEN_EXPIRE_HOURS=24
    volumes:
      # 持久化: 管理员账号 / JWT 密钥 / 小米账号与音箱配置
      - ./data:/app/data
