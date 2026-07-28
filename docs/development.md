# 本地开发

## 后端(FastAPI)

```bash
cd backend
python run.py          # 自动安装缺失依赖并在 :8300 启动
```

## 前端(Vue 3 + Vite)

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
└── .github/workflows/       # CI: 云端多架构镜像发布 + GitHub Release
```
