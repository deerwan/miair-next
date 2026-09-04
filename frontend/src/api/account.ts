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

export type AccountStatusLevel = 'offline' | 'expired' | 'expiring' | 'healthy'

export interface AccountStatus {
  user_id: string
  logged_in: boolean
  status: AccountStatusLevel
  service_token_remaining_hours: number | null
  has_password_fallback: boolean
  token_refresh_running: boolean
  has_account: boolean
}

/** 当前小米账号登录状态 (状态卡展示用) */
export async function fetchAccountStatus(): Promise<AccountStatus> {
  const { data } = await http.get('/account/status')
  return data
}

/** 删除账号: 清空所有凭证并热重启服务, 回到未配置状态 */
export async function deleteAccount(): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.delete('/account')
  return data
}
