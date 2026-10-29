import { ref } from 'vue'
import type { SpotifySpeakerStatus } from '@/api/speakers'
import { useAuthStore } from '@/stores/auth'
import { fetchMe } from '@/api/auth'
import router from '@/router'

export interface NowPlaying {
  playing: boolean
  source: '' | 'dlna' | 'airplay' | 'spotify'
  title: string
  artist: string
  client: string
  album?: string
  artwork_id?: string
}

export interface StatusMessage {
  type: 'status'
  dlna_running: boolean
  renderers_count: number
  speakers: {
    did: string
    dlna_name: string
    transport_state: string
    current_uri: string
    airplay_active: boolean
    airplay_audio?: {
      protocol?: 'airplay1' | 'airplay2'
      playing: boolean
      sample_rate: number
      channels: number
      rtp_packets_received?: number
      rtp_queue_drops?: number
      decode_success?: number
      decode_errors?: number
      decode_empty_packets?: number
      decrypt_failures?: number
      lost_frames_filled?: number
      pcm_bytes?: number
      last_rtp_packet_age_seconds?: number | null
      last_decode_error?: string
      rtp_queue_depth?: number
      stream_clients: number
      stream_queue_depth: number
      stream_pending_dropped_bytes: number
    } | null
    now_playing?: NowPlaying
    spotify?: SpotifySpeakerStatus
  }[]
}

export interface LogMessage {
  type: 'log'
  line: string
}

// —— 全局单例状态: 所有视图复用同一条连接 ——
// 原实现每次调用 useWebSocket() 都新建 WS, 组件卸载即断开重连,
// 状态与日志随路由切换全部丢失; 提升为模块级单例后, Dashboard
// 与 Logs 共享一份 status/logLines, 切换页面零成本、日志不丢。
const connected = ref(false)
const status = ref<StatusMessage | null>(null)
const logLines = ref<string[]>([])

let ws: WebSocket | null = null
let retryTimer: number | null = null
// 指数退避: 断线重连 3s 起步、逐次翻倍、上限 30s, 避免后端
// 长时间宕机时浏览器高频敲门; 连接成功后重置
const RETRY_BASE_MS = 3000
const RETRY_MAX_MS = 30000
let retryDelay = RETRY_BASE_MS
// 本次连接是否成功 open 过: 用于区分「握手即被拒」和「已连上后断线」
let everOpened = false
// 主动断开标志 (登出时置位): onclose 据此跳过自动重连
let manualClose = false
// 本次页面会话内是否成功建立过连接: 重连时后端会回放历史日志,
// 需先清空本地旧内容, 否则回放内容与断线前收到的日志重复拼接
let hasConnectedBefore = false

function connect() {
  const auth = useAuthStore()
  if (!auth.token) return
  if (ws) return // 幂等: 已连接或连接中
  everOpened = false
  manualClose = false

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/v1/ws?token=${auth.token}`)

  ws.onopen = () => {
    everOpened = true
    retryDelay = RETRY_BASE_MS
    connected.value = true
    // 重连场景: 后端订阅时会回放历史日志, 清掉本地旧内容
    // (回放携带断线期间的全部新日志, 清空后由回放补全)
    if (hasConnectedBefore) {
      logLines.value = []
    }
    hasConnectedBefore = true
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'status') {
        status.value = msg
      } else if (msg.type === 'log') {
        logLines.value.push(msg.line)
        if (logLines.value.length > 500) logLines.value.shift()
      }
    } catch {
      /* 忽略非法消息 */
    }
  }
  ws.onclose = (ev) => {
    connected.value = false
    // 主动断开 (登出): 不重连
    if (manualClose) {
      manualClose = false
      return
    }
    // 4401: token 无效/过期, 不再重连, 清登录态回登录页 (避免无限重连被拒)
    if (ev.code === 4401) {
      const auth = useAuthStore()
      auth.logout()
      if (router.currentRoute.value.name !== 'login') {
        router.push({ name: 'login' })
      }
      return
    }
    // 1006 且从未 open: 握手即被拒。旧版后端在 accept 前关闭会被
    // uvicorn 转成 HTTP 403 (浏览器只见 1006), 用 /me 复核登录态,
    // 401 时 axios 拦截器会统一登出并跳转登录页
    if (ev.code === 1006 && !everOpened) {
      fetchMe().catch(() => {})
    }
    // 其它原因断线: 指数退避自动重连
    retryTimer = window.setTimeout(connect, retryDelay)
    retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS)
  }
  ws.onerror = () => ws?.close()
}

/** 主动断开连接并取消待执行的重连 (登出时由 auth store 调用) */
export function disconnectWebSocket() {
  manualClose = true
  hasConnectedBefore = false
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  ws?.close()
  ws = null
  connected.value = false
  retryDelay = RETRY_BASE_MS
  // 清理残留数据: 下一个登录会话重新由回放补全, 避免旧会话内容
  // 与回放历史重复拼接
  status.value = null
  logLines.value = []
}

/** 连接后端 WebSocket, 实时接收状态与日志 (token 通过查询参数传递) */
export function useWebSocket() {
  connect()
  return { connected, status, logLines }
}
