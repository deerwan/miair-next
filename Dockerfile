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

# 音频转码、网络探测与 Soloist/PulseAudio 运行依赖;
# gosu 供入口脚本降权使用, libcap2-bin 提供 setcap (为非 root 用户授权
# 绑定 SSDP 1900/udp 与 mDNS 5353/udp 特权端口)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libportaudio2 \
    dnsutils \
    libatomic1 \
    pulseaudio \
    pulseaudio-utils \
    gosu \
    libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# 依赖: 按 requirements.lock 固定版本安装 (uv pip compile 生成), 保证构建可复现
COPY backend/pyproject.toml backend/requirements.lock ./
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.lock --root-user-action=ignore

COPY backend/ ./
COPY --from=frontend /web/dist/ ./app/static/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

ENV MIAIR_DATA=/app/data \
    MIAIR_WEB_HOST=0.0.0.0 \
    MIAIR_WEB_PORT=8300 \
    MIAIR_GITHUB_REPO=deerwan/miair-next \
    XDG_RUNTIME_DIR=/tmp/runtime-miair

# 非 root 运行 (安全基线): SSDP (1900/udp) 与 mDNS (5353/udp) 需绑定特权端口,
# 通过 file capability 为 python 二进制授权; 数据目录属主由 entrypoint 修正
RUN groupadd -r miair && useradd -r -g miair miair \
    && setcap cap_net_bind_service=+ep "$(readlink -f "$(which python3)")" \
    && mkdir -p /app/data /tmp/runtime-miair \
    && chown -R miair:miair /app/data /tmp/runtime-miair
VOLUME ["/app/data"]

# 8300: Web 管理后台 / API
# DLNA (SSDP 1900/udp) 与 AirPlay (mDNS 5353/udp) 需要 host 网络, 见 README
EXPOSE 8300

# 健康检查: /api/v1/health 无需鉴权; 镜像内无 curl, 用 python 标准库探测
# (uvicorn 在 lifespan 启动完成后才监听端口, start-period 给编排器留启动时间)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MIAIR_WEB_PORT', '8300') + '/api/v1/health', timeout=4)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh", "python", "run.py"]
