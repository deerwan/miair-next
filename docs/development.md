# 本地开发

## 一键开发脚本 (推荐)

```bash
./dev.sh start     # 启动前后端 (自动建 venv / 装依赖 / 释放端口)
./dev.sh stop      # 停止并按 PID + 端口兜底清理
./dev.sh restart   # 重启
./dev.sh status    # 查看运行状态
```

- 后端运行在 `:8300` (开发模式热重载, 仅监听 `app/` 目录变更), 前端 Vite 运行在 `:5173` (`/api` 已代理到后端, 含 WebSocket)。
- AirPlay 绑定 IP 默认取 en0 网卡, 多网卡环境下可用 `MIAIR_HOSTNAME=<IP> ./dev.sh start` 覆盖。
- 日志固定输出到 `/tmp/miair-logs/backend.log` 与 `/tmp/miair-logs/frontend.log`。

> ⚠️ **请勿在终端手动运行 `python run.py` / `uvicorn` 启动开发服务**:
> 异常退出 (直接关终端、Ctrl-C 被吞) 会遗留孤儿进程, 继续占用 8300/8200
> 端口与 SSDP/mDNS 组播套接字, 导致新实例 DLNA 起不来
> (`Errno 48 address already in use`)。排查: `lsof -nP -i :8300` (8200/5173 同理)。
> 下面的手动方式仅用于需要自定义启动参数的场景。

## 后端(FastAPI) — 手动方式

```bash
cd backend
python run.py          # 自动安装缺失依赖并在 :8300 启动
```

## 前端(Vue 3 + Vite) — 手动方式

```bash
cd frontend
npm install
npm run dev            # 开发服务器 :5173, 已配置 /api 代理到 :8300 (含 WebSocket)
```

生产构建(产物需拷贝到 `backend/app/static/` 由后端托管):

```bash
cd frontend
npm run build
```

## 测试

```bash
cd backend
pip install -e ".[dev]"   # 安装 pytest
python -m pytest tests/ -v
```

## 本地构建镜像

```bash
docker build -t miair-next:local .
```

## 发版流程

1. 同步修改版本号(两处需一致):`backend/app/__init__.py` 的 `__version__` 与 `backend/pyproject.toml` 的 `version`,提交并推送 `main`
2. 打语义化 tag 并推送:

```bash
git tag v0.1.0
git push origin v0.1.0
```

CI 自动完成:构建推送 Docker 镜像(`latest` / `0.1.0` / `0.1` / `<sha>`)+ 创建 GitHub Release(供「检查更新」检测)。

## 项目结构

```
miair-next/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── core/            # 应用级配置 / 安全 / 日志 / 脱敏
│   │   ├── engine/          # 协议层 (DLNA / AirPlay / 小米云, 移植自 MiAir)
│   │   ├── services/        # 编排器 (Orchestrator)
│   │   ├── models/          # Pydantic 模型
│   │   ├── api/v1/          # REST + WebSocket 接口
│   │   ├── db/              # SQLite 用户存储
│   │   └── main.py          # FastAPI 入口 (含 SPA 托管)
│   ├── tests/               # pytest 测试
│   ├── run.py               # 启动脚本
│   └── pyproject.toml
├── frontend/                # Vue 3 + Vite + Naive UI 管理后台
│   └── src/
│       ├── api/             # axios 封装
│       ├── stores/          # Pinia
│       ├── router/          # 路由 + 守卫
│       ├── layouts/         # 后台布局 (可折叠侧边栏)
│       ├── composables/     # useWebSocket 等
│       └── views/           # 登录 / 总览 / 设备 / 播放 / 账号 / 设置 / 日志
├── docs/                    # 文档 (接口 / 部署 / 开发)
├── Dockerfile               # 多阶段构建 (前端 -> Python 运行时)
├── docker-compose.yml
├── install.sh               # 一键安装脚本
├── manage.sh                # 容器管理脚本 (start/stop/update...)
├── dev.sh                   # 本地一键开发脚本 (前后端启停 / 日志归集)
└── .github/workflows/       # CI: 云端多架构镜像发布 + GitHub Release
```
