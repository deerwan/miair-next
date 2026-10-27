# 📦 依赖组件

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
