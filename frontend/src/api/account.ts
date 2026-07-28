import http from './http'

export interface QRCodeStart {
  success: boolean
  session_id?: string
  qrcode_url?: string
  login_url?: string
  error?: string
}

export type QRPollState = 'waiting' | 'confirmed' | 'expired' | 'failed'

export interface QRPollResult {
  success: boolean
  state: QRPollState
  message: string
  user_id?: string
}

/** 启动扫码登录, 获取二维码与会话 ID */
export async function startQRCode(): Promise<QRCodeStart> {
  const { data } = await http.post('/account/qrcode')
  return data
}

/** 轮询扫码状态 (后端为长轮询, 单次请求可能挂起约 30s) */
export async function pollQRCode(sessionId: string): Promise<QRPollResult> {
  const { data } = await http.get('/account/qrcode/poll', {
    params: { session_id: sessionId },
    // 长轮询: 放宽超时, 覆盖 http.ts 默认值
    timeout: 40000,
  })
  return data
}
