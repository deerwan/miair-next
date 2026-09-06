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

// ===== 密码登录 (交互式: 登录 → 验证码/短信验证 → 成功) =====

export type PasswordLoginState = 'success' | 'need_captcha' | 'need_verify' | 'failed'

export interface PasswordLoginResult {
  success: boolean
  state?: PasswordLoginState
  session_id?: string
  captcha_image?: string
  verify_type?: 'phone' | 'email'
  message?: string
  error?: string
}

/** 密码登录: 成功 / 需要图形验证码 / 需要短信邮箱验证 / 失败 */
export async function passwordLogin(username: string, password: string): Promise<PasswordLoginResult> {
  const { data } = await http.post('/account/login', { username, password })
  return data
}

/** 提交图形验证码, 继续密码登录流程 */
export async function submitLoginCaptcha(sessionId: string, captcha: string): Promise<PasswordLoginResult> {
  const { data } = await http.post('/account/login/captcha', { session_id: sessionId, captcha })
  return data
}

/** 提交短信/邮箱验证码, 完成密码登录流程 */
export async function submitLoginVerifyCode(sessionId: string, code: string): Promise<PasswordLoginResult> {
  const { data } = await http.post('/account/login/verify', { session_id: sessionId, code })
  return data
}

/** 手动 Token: 立即用 passToken 换取 serviceToken 验证有效性 */
export async function setManualToken(
  userId: string,
  passToken: string,
): Promise<{ success: boolean; message?: string; error?: string }> {
  const { data } = await http.post('/account/token', { user_id: userId, pass_token: passToken })
  return data
}
