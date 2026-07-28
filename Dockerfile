# syntax=docker/dockerfile:1

# ---------- 阶段 1: 构建前端 (Vue3 + Vite) ----------
FROM node:20-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2: Python 运行时 ----------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="MiAir Next"
LABEL org.opencontainers.image.description="Make Xiaomi AI speakers act as DLNA / AirPlay renderers, with a modern admin panel"
LABEL org.opencontainers.image.licenses="MIT"

# 协议层依赖: ffmpeg (av 音频转码), libportaudio2, dnsutils
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libportaudio2 \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# 先装依赖 (利用缓存层)
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir . --root-user-action=ignore

# 拷贝后端源码
COPY backend/ ./

# 拷贝前端构建产物到后端静态目录 (由 FastAPI 托管 SPA)
COPY --from=frontend /web/dist/ ./app/static/

# 数据目录 (SQLite / JWT 密钥 / 引擎配置持久化)
ENV MIAIR_DATA=/app/data \
    MIAIR_WEB_HOST=0.0.0.0 \
    MIAIR_WEB_PORT=8300 \
    MIAIR_GITHUB_REPO=deerwan/miair-next
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# 8300: Web 管理后台 / API
# DLNA (SSDP 1900/udp) 与 AirPlay (mDNS 5353/udp) 需要 host 网络, 见 README
EXPOSE 8300

ENTRYPOINT ["python", "run.py"]
