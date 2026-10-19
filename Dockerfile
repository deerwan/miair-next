ARG NODE_IMAGE=node:20-alpine
ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ${NODE_IMAGE} AS frontend
ARG NPM_REGISTRY=
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm install
COPY frontend/ ./
RUN npm run build

# Soloist 二进制依赖 glibc >= 2.38 (Debian trixie)
FROM ${PYTHON_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple

LABEL org.opencontainers.image.title="MiAir Next"
LABEL org.opencontainers.image.description="Make Xiaomi AI speakers act as DLNA / AirPlay renderers, with a modern admin panel"
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"

# 音频转码、网络探测与 Soloist/PulseAudio 运行依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libportaudio2 \
    dnsutils \
    libatomic1 \
    pulseaudio \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" . --root-user-action=ignore

COPY backend/ ./
COPY --from=frontend /web/dist/ ./app/static/

ENV MIAIR_DATA=/app/data \
    MIAIR_WEB_HOST=0.0.0.0 \
    MIAIR_WEB_PORT=8300 \
    MIAIR_GITHUB_REPO=deerwan/miair-next
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# 8300: Web 管理后台 / API
# DLNA (SSDP 1900/udp) 与 AirPlay (mDNS 5353/udp) 需要 host 网络, 见 README
EXPOSE 8300

# 健康检查: /api/v1/health 无需鉴权; 镜像内无 curl, 用 python 标准库探测
# (uvicorn 在 lifespan 启动完成后才监听端口, start-period 给编排器留启动时间)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MIAIR_WEB_PORT', '8300') + '/api/v1/health', timeout=4)"

ENTRYPOINT ["python", "run.py"]
