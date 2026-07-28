import { onUnmounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

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

  function connect() {
    const auth = useAuthStore()
    if (!auth.token) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/v1/ws?token=${auth.token}`)

    ws.onopen = () => {
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
    ws.onclose = () => {
      connected.value = false
      // 断线自动重连
      retryTimer = window.setTimeout(connect, 3000)
    }
    ws.onerror = () => ws?.close()
  }

  function disconnect() {
    if (retryTimer) clearTimeout(retryTimer)
    ws?.close()
    ws = null
  }

  connect()
  onUnmounted(disconnect)

  return { connected, status, logLines }
}
