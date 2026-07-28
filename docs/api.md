# MiAir Next 接口地址文档

后端基于 FastAPI,默认监听端口 `8300`(可通过环境变量 `MIAIR_WEB_PORT` 修改)。

## 基础地址

| 用途 | 地址 |
| --- | --- |
| REST API 基础前缀 | `http://<主机>:8300/api/v1` |
| Swagger 交互文档 | `http://<主机>:8300/api/docs` |
| OpenAPI JSON | `http://<主机>:8300/api/openapi.json` |
| 前端管理界面 (SPA) | `http://<主机>:8300/` |
| WebSocket 实时推送 | `ws://<主机>:8300/api/v1/ws?token=<JWT>` |

> Docker 部署使用 host 网络,`<主机>` 即宿主机 IP;本地开发前端 dev 服务器 (`:5173`) 已配置 `/api` 代理(含 WS)到 `:8300`。

## 鉴权说明

- 业务接口需在请求头携带 `Authorization: Bearer <token>`,token 由登录接口签发(JWT)。
- WebSocket 无法携带 Header,改用查询参数 `?token=<JWT>` 校验。

## 接口清单

### 公开接口(无需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查(Docker healthcheck 使用) |
| GET | `/api/v1/login/status` | 是否已初始化管理员(前端首次引导) |
| POST | `/api/v1/login/setup` | 首次设置管理员账号(仅未初始化时可用) |
| POST | `/api/v1/login` | 登录,签发 JWT(带失败限速防暴力破解) |

### 用户(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/me` | 当前用户信息(前端校验 token 有效性) |
| POST | `/api/v1/login/password` | 修改密码 |

### 账号配置(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/settings` | 读取配置(`?need_device_list=true` 附带设备列表) |
| POST | `/api/v1/settings` | 保存配置并热重启子服务(cookie 支持脱敏回写还原) |
| POST | `/api/v1/account/qrcode` | 启动小米扫码登录,返回二维码与会话 ID |
| GET | `/api/v1/account/qrcode/poll` | 轮询扫码结果(`?session_id=<id>`) |

### 设备与播放控制(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/devices` | 小米账号下所有设备列表 |
| GET | `/api/v1/speakers` | 运行中的渲染器状态(含兼容模式) |
| POST | `/api/v1/speakers/{did}/rename` | 重命名 DLNA 显示名 |
| POST | `/api/v1/speakers/{did}/play_url` | 播放指定 URL |
| POST | `/api/v1/speakers/{did}/pause` | 暂停 |
| POST | `/api/v1/speakers/{did}/stop` | 停止 |
| GET | `/api/v1/speakers/{did}/volume` | 查询音量 |
| POST | `/api/v1/speakers/{did}/volume` | 设置音量 |
| GET | `/api/v1/speakers/{did}/status` | 查询播放状态 |

### 系统(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/status` | 服务整体状态 |
| GET | `/api/v1/logs` | 最近日志(`?limit=200`,内存环形缓冲) |
| GET | `/api/v1/system/check_update` | 检查 GitHub 最新 Release 是否有新版本 |
| POST | `/api/v1/system/restart_services` | 热重启 DLNA/AirPlay 子服务 |
| POST | `/api/v1/system/restart_process` | 重启整个进程(Docker 下由容器策略拉起) |

### WebSocket

| 路径 | 说明 |
| --- | --- |
| `/api/v1/ws?token=<JWT>` | 实时推送音箱状态与运行日志 |

## 快速验证

```bash
# 健康检查 (无需鉴权)
curl http://localhost:8300/api/v1/health

# 登录拿 token
curl -X POST http://localhost:8300/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<密码>"}'

# 携带 token 调业务接口
curl http://localhost:8300/api/v1/speakers \
  -H "Authorization: Bearer <token>"
```

> 完整的请求/响应模型请以 Swagger 文档 `http://<主机>:8300/api/docs` 为准。
