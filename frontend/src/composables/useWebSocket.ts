import { onUnmounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { fetchMe } from '@/api/auth'
import router from '@/router'

export interface NowPlaying {
  playing: boolean
  source: '' | 'dlna' | 'airplay'
  title: string
  artist: string
  client: string
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
    /** 正在播放摘要 (旧后端可能不带, 可选) */
    now_playing?: NowPlaying
  }[]
}

export interface LogMessage {
  type: 'log'
  line: string
}

/** 连接后端 WebSocket, 实时接收状态与日志 (token 通过查询参数传递) */
export function useWebSocket() {
  const connected = ref(false)
  const status = ref<StatusMessage | null>(null)
  const logLines = ref<string[]>([])
  let ws: WebSocket | null = null
  let retryTimer: number | null = null
  // 组件卸载标志: disconnect() 关闭的连接稍后仍会触发 onclose,
  // 不短路的话每进一次页面就泄漏一条每 3 秒自动重连的幽灵连接
  let disposed = false
  // 本次连接是否成功 open 过: 用于区分「握手即被拒」和「已连上后断线」
  let everOpened = false

  function connect() {
    if (disposed) return
    const auth = useAuthStore()
    if (!auth.token) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/v1/ws?token=${auth.token}`)

    ws.onopen = () => {
      everOpened = true
      connected.value = true
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
      if (disposed) return
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
      // 其它原因断线自动重连
      retryTimer = window.setTimeout(connect, 3000)
    }
    ws.onerror = () => ws?.close()
  }

  function disconnect() {
    disposed = true
    if (retryTimer) clearTimeout(retryTimer)
    ws?.close()
    ws = null
  }

  connect()
  onUnmounted(disconnect)

  return { connected, status, logLines }
}
