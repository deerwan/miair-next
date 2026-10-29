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
- 修改密码会轮换 JWT 密钥,立即吊销所有已签发 token(含当前会话),客户端随后会收到 `401` 并需重新登录。
- 登录失败按客户端 IP 限速(默认连续 5 次锁定 600 秒);仅当 `MIAIR_TRUST_PROXY` 开启时才采信 `X-Forwarded-For`,直连部署下请勿开启,否则伪造该头可绕过限速。

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
| POST | `/api/v1/login/password` | 修改密码(同时轮换 JWT 密钥,吊销全部旧 token) |
| GET | `/api/v1/login/logs` | 最近的面板登录记录(`?limit=50`,上限 100,记录内保留最近 100 条) |

### 系统设置(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/settings` | 读取配置(`?need_device_list=true` 附带设备列表) |
| POST | `/api/v1/settings` | 保存配置并热重启子服务(cookie 支持脱敏回写还原) |

保存设置后返回即触发后台热重启(不重启进程),响应中的 `message` 仅表示已受理。

### 小米账号(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/account/qrcode` | 启动扫码登录,返回二维码与会话 ID |
| GET | `/api/v1/account/qrcode/poll` | 轮询扫码结果(`?session_id=<id>`) |
| POST | `/api/v1/account/login` | 账号密码登录,可能返回需图形验证码/短信邮箱验证 |
| POST | `/api/v1/account/login/captcha` | 提交图形验证码,继续密码登录流程 |
| POST | `/api/v1/account/login/verify` | 提交短信/邮箱验证码,完成密码登录流程 |
| POST | `/api/v1/account/token` | 手动提交 `userId` + `passToken`,立即验证并换取 serviceToken |
| GET | `/api/v1/account/status` | 当前登录状态(`offline` / `expired` / `expiring` / `healthy`) |
| DELETE | `/api/v1/account` | 清除全部登录凭证并热重启,回到未配置状态 |

密码登录是多步交互,`state` 字段决定后续走向:

| `state` | 含义 | 下一步 |
| --- | --- | --- |
| `success` | 登录成功,凭证已落盘 | 无需续步,服务热重启中 |
| `need_captcha` | 需要图形验证码 | 带 `session_id` + `captcha` 调 `/account/login/captcha` |
| `need_verify` | 需要短信/邮箱验证码 | 带 `session_id` + `code` 调 `/account/login/verify` |
| `failed` | 失败 | 查看 `error` |

> `passToken` / `serviceToken` 全程留在后端,不返回给前端。扫码或密码登录成功后,后端将凭证写入 `config.json` 与 `.mi.token`(权限 `600`)并热重启 DLNA/AirPlay 服务。

### 设备与播放控制(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/devices` | 小米账号下所有设备列表 |
| GET | `/api/v1/speakers` | 运行中的渲染器状态(含兼容模式与 Spotify 状态) |
| POST | `/api/v1/speakers/{did}/rename` | 重命名 DLNA 显示名(同步重建 AirPlay mDNS 广播) |
| POST | `/api/v1/speakers/{did}/play_url` | 播放指定 URL(仅接受 http/https) |
| POST | `/api/v1/speakers/{did}/stop` | 停止播放 |
| GET | `/api/v1/speakers/{did}/volume` | 查询音量 |
| POST | `/api/v1/speakers/{did}/volume` | 设置音量(0-100) |
| GET | `/api/v1/speakers/{did}/status` | 查询播放状态 |
| GET | `/api/v1/speakers/{did}/airplay/artwork/{artwork_id}` | Current AirPlay 2 JPEG/PNG artwork; Bearer authentication required, 404 when absent, replaced, or inactive. `artwork_id` comes from WebSocket `now_playing`. |

> **Web 面板不提供「暂停」接口**。DLNA 控制点(App)的暂停由协议层直接处理 —— 走 SOAP `AVTransport/Pause`,内部会中止媒体代理连接使音箱停止拉流,再经小米云下发暂停。Web 面板只暴露播放/停止,这是有意的产品裁剪(见提交 `9e0ed7e`「移除无用暂停停止接口及相关前端调用」),不是缺失。

### Spotify 畅联(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/spotify/status` | 运行/配对状态、当前曲目、Soloist 版本与剩余有效期 |
| POST | `/api/v1/spotify/pair` | 返回配对引导(在 Spotify App 设备列表选择设备名) |
| POST | `/api/v1/spotify/restart` | 重启 Soloist 接收器 |
| POST | `/api/v1/spotify/update` | 后台重新下载最新 Soloist 二进制并重启 |

### 系统与日志(需鉴权)

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/status` | 服务整体状态(运行时长 / 内存 / CPU / 磁盘 / token 剩余有效期) |
| GET | `/api/v1/logs` | 最近日志(`?limit=200`,内存环形缓冲,最多保留 500 条) |
| GET | `/api/v1/logs/download` | 下载落盘的完整日志文件(`miair.log`) |
| GET | `/api/v1/logs/level` | 查询当前日志等级 |
| POST | `/api/v1/logs/level` | 运行时切换日志等级(`debug` / `info` / `warning` / `error`,重启后恢复) |
| GET | `/api/v1/system/check_update` | 检查 GitHub 最新 Release 是否有新版本(`?force=true` 绕过 10 分钟缓存) |
| POST | `/api/v1/system/restart_services` | 热重启 DLNA/AirPlay 子服务 |
| POST | `/api/v1/system/restart_process` | 重启整个进程(Docker 下由容器策略拉起) |
| POST | `/api/v1/system/notify/test` | 向已配置的通知渠道发送测试消息(跳过节流) |

### WebSocket

| 路径 | 说明 |
| --- | --- |
| `/api/v1/ws?token=<JWT>` | 实时推送音箱状态(每 3 秒,含 AirPlay 音频诊断)与运行日志；AirPlay 2 提供桥接格式、队列和拉流连接指标，省略不可获取的原生 RTP/解码计数 |

token 失效时服务端主动以 `4401` 关闭连接;前端收到 `4401` 后停止重连并跳转登录页。每 60 秒复验一次 token,过期即断开。

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

# 查看账号登录状态
curl http://localhost:8300/api/v1/account/status \
  -H "Authorization: Bearer <token>"
```

> 完整的请求/响应模型请以 Swagger 文档 `http://<主机>:8300/api/docs` 为准。
> `backend/tests/test_api_docs_sync.py` 会自动比对本文档与 OpenAPI schema,新增或删除路由时若未同步更新表格,测试会失败。
