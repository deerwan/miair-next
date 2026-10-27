# 🚀 快速开始（Docker，推荐）

> ⚠️ **必须使用 host 网络**：DLNA/AirPlay 依赖组播发现，仅支持 **Linux 宿主机**。详见[部署与配置](deployment.md)。

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
