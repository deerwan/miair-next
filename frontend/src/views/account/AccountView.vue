<template>
  <n-space vertical :size="16">
    <n-card v-if="status.logged_in" title="当前账号状态">
      <n-space align="center" justify="space-between" :wrap="false">
        <div>
          <n-space align="center" :size="8">
            <n-text strong style="font-size: 20px">{{ status.user_id }}</n-text>
            <n-tag :type="statusTagType" round size="small">{{ statusLabel }}</n-tag>
          </n-space>
          <div style="margin-top: 4px">
            <n-text depth="3" style="font-size: 13px">{{ statusSubtitle }}</n-text>
          </div>
        </div>
        <n-space>
          <n-button circle :loading="refreshing" :disabled="!status.has_account" title="刷新重新登录" @click="refreshAccount">
            <template #icon>
              <n-icon>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="23 4 23 10 17 10" />
                  <polyline points="1 20 1 14 7 14" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
              </n-icon>
            </template>
          </n-button>
          <n-button circle type="error" :loading="deleting" :disabled="!status.has_account" title="删除账号" @click="confirmDelete">
            <template #icon>
              <n-icon>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6" />
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  <line x1="10" y1="11" x2="10" y2="17" />
                  <line x1="14" y1="11" x2="14" y2="17" />
                </svg>
              </n-icon>
            </template>
          </n-button>
        </n-space>
      </n-space>
    </n-card>

    <n-card title="登录小米账号">
      <n-tabs v-model:value="activeMethod" type="segment" animated>
        <!-- 方式一: 扫码登录 -->
        <n-tab-pane name="qr" tab="扫码登录">
          <n-alert type="success" :bordered="false" style="margin-bottom: 16px">
            推荐。使用米家 App 扫码, 自动获取登录凭证, 无需手动抓取 Cookie, 也不会触发验证码。
          </n-alert>

          <n-space v-if="!qr.sessionId" vertical>
            <n-button type="primary" :loading="qr.starting" @click="startQr">获取二维码</n-button>
            <n-text v-if="qr.error" type="error">{{ qr.error }}</n-text>
          </n-space>

          <n-space v-else vertical align="center">
            <img
              v-if="qr.qrcodeUrl"
              :src="qr.qrcodeUrl"
              alt="小米登录二维码"
              style="width: 200px; height: 200px; border: 1px solid var(--n-border-color)"
            />
            <n-space align="center" :size="8">
              <n-spin v-if="qr.state === 'waiting'" :size="14" />
              <n-text :depth="qr.state === 'waiting' ? 3 : 1">{{ qr.statusText }}</n-text>
            </n-space>
            <n-button size="small" @click="resetQr">重新获取</n-button>
          </n-space>
        </n-tab-pane>

        <!-- 方式二: 账号密码 -->
        <n-tab-pane name="password" tab="账号密码">
          <n-form label-placement="top">
            <n-form-item label="账号 (手机号 / 邮箱 / 小米 ID)">
              <n-input v-model:value="form.account" placeholder="请输入账号" />
            </n-form-item>
            <n-form-item label="密码">
              <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="请输入密码" />
            </n-form-item>
          </n-form>
          <n-space vertical :size="10">
            <n-space align="center" :size="10">
              <n-button type="primary" :loading="pw.loading" @click="startPasswordLogin">
                登录并应用
              </n-button>
              <n-text depth="3" style="font-size: 12px">
                立即登录并启用; 触发验证码/短信验证时在下方完成
              </n-text>
            </n-space>

            <!-- 图形验证码续步 -->
            <n-space v-if="pw.state === 'need_captcha'" align="center" :size="12">
              <img
                v-if="pw.captchaImage"
                :src="`data:image/png;base64,${pw.captchaImage}`"
                alt="图形验证码"
                style="height: 40px; border: 1px solid var(--n-border-color); border-radius: 4px"
              />
              <n-input
                v-model:value="pw.captchaCode"
                placeholder="请输入图中验证码"
                style="width: 180px"
                @keyup.enter="submitPwCaptcha"
              />
              <n-button :loading="pw.loading" @click="submitPwCaptcha">提交</n-button>
            </n-space>

            <!-- 短信/邮箱验证码续步 -->
            <n-space v-else-if="pw.state === 'need_verify'" align="center" :size="12">
              <n-text>验证码已发送至{{ pw.verifyType === 'email' ? '邮箱' : '手机' }}</n-text>
              <n-input
                v-model:value="pw.verifyCode"
                placeholder="请输入验证码"
                style="width: 180px"
                @keyup.enter="submitPwVerify"
              />
              <n-button :loading="pw.loading" @click="submitPwVerify">提交</n-button>
            </n-space>

            <n-text v-if="pw.error" type="error">{{ pw.error }}</n-text>
          </n-space>
          <n-checkbox v-model:checked="clearCookie" style="margin-top: 12px">
            保存时清空已保存的 Cookie (用于强制改用账密登录)
          </n-checkbox>
        </n-tab-pane>

        <!-- 方式三: 手动 Token -->
        <n-tab-pane name="token" tab="手动 Token">
          <n-alert :show-icon="false" :bordered="false" style="margin-bottom: 16px">
            从浏览器 Cookie 中获取 userId 和 passToken。凭据只发送给本服务，不会显示在日志中。
          </n-alert>
          <n-form label-placement="top">
            <n-form-item label="User ID">
              <n-input v-model:value="form.userId" placeholder="Cookie 中的 userId" />
            </n-form-item>
            <n-form-item label="Pass Token">
              <n-input
                v-model:value="form.passToken"
                type="password"
                show-password-on="click"
                placeholder="Cookie 中的 passToken"
              />
            </n-form-item>
          </n-form>
          <n-space vertical :size="8">
            <n-space align="center" :size="10">
              <n-button type="primary" :loading="tokenVerifying" @click="verifyManualToken">
                验证并应用
              </n-button>
              <n-text depth="3" style="font-size: 12px">
                立即换取 serviceToken 验证有效性并启用 (填错当场报错)
              </n-text>
            </n-space>
            <n-text v-if="tokenError" type="error">{{ tokenError }}</n-text>
          </n-space>
        </n-tab-pane>
      </n-tabs>
    </n-card>

    <n-card title="选择音箱设备">
      <template #header-extra>
        <n-button circle size="small" :loading="loadingDevices" :disabled="!status.has_account" title="刷新设备列表" @click="loadDevices">
          <template #icon>
            <n-icon>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="23 4 23 10 17 10" />
                <polyline points="1 20 1 14 7 14" />
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
              </svg>
            </n-icon>
          </template>
        </n-button>
      </template>
      <n-alert v-if="deviceError" type="warning" :bordered="false">{{ deviceError }}</n-alert>
      <n-checkbox-group v-model:value="selectedDids">
        <n-space vertical>
          <n-checkbox v-for="d in devices" :key="d.miotDID" :value="d.miotDID">
            {{ d.name }} <n-text depth="3">({{ d.hardware }})</n-text>
          </n-checkbox>
        </n-space>
      </n-checkbox-group>
      <n-empty v-if="devices.length === 0" description="暂无可用音箱设备" />
    </n-card>

    <n-button type="primary" :loading="saving" @click="save">保存并应用 (将重启服务)</n-button>
  </n-space>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref, computed } from 'vue'
import {
  NSpace, NCard, NForm, NFormItem, NInput, NButton, NAlert, NCheckbox,
  NCheckboxGroup, NText, NEmpty, NSpin, NTabs, NTabPane, NTag, NIcon, useMessage, useDialog,
} from 'naive-ui'
import { fetchSettings, saveSettings, restartServices } from '@/api/system'
import { fetchCloudDevices, type CloudDevice } from '@/api/speakers'
import {
  startQRCode,
  pollQRCode,
  fetchAccountStatus,
  deleteAccount,
  passwordLogin,
  submitLoginCaptcha,
  submitLoginVerifyCode,
  setManualToken,
  type QRPollState,
  type AccountStatus,
  type AccountStatusLevel,
  type PasswordLoginResult,
} from '@/api/account'

const message = useMessage()
const dialog = useDialog()

// 当前账号状态卡 (仅在已登录时展示, 简洁风格)
const status = reactive<AccountStatus>({
  user_id: '',
  logged_in: false,
  status: 'offline',
  service_token_remaining_hours: null,
  has_password_fallback: false,
  token_refresh_running: false,
  has_account: false,
})
const refreshing = ref(false)
const deleting = ref(false)

const statusTagType = computed(() => {
  switch (status.status) {
    case 'healthy': return 'success'
    case 'expiring': return 'warning'
    case 'expired': return 'error'
    default: return 'default'
  }
})
const statusLabelMap: Record<AccountStatusLevel, string> = {
  healthy: '正常', expiring: '即将过期', expired: '已过期', offline: '未登录',
}
const statusLabel = computed(() => statusLabelMap[status.status])
const statusSubtitle = computed(() => {
  if (status.status === 'expiring') return '凭证即将过期, 建议点击「刷新重新登录」'
  if (status.status === 'expired') return '凭证已过期, 请点击「刷新重新登录」'
  return `已登录 · User ID ${status.user_id}`
})

async function loadAccountStatus() {
  try {
    Object.assign(status, await fetchAccountStatus())
  } catch {
    // 忽略瞬时错误 (如刚删除账号时服务短暂重启)
  }
}

async function refreshAccount() {
  refreshing.value = true
  try {
    await restartServices()
    message.success('已触发重新登录, 稍后状态将更新')
    await loadAccountStatus()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '刷新失败')
  } finally {
    refreshing.value = false
  }
}

async function confirmDelete() {
  dialog.warning({
    title: '删除账号',
    content: '将清空所有登录凭证 (Cookie / 账号密码) 并停止 DLNA 投送服务, 需重新扫码或填账密登录。确定继续?',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: doDelete,
  })
}

async function doDelete() {
  deleting.value = true
  try {
    await deleteAccount()
    message.success('账号已删除, 服务已重置')
    await loadAccountStatus()
    await loadInit()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  } finally {
    deleting.value = false
  }
}

// 当前登录方式: qr(扫码) / password(账号密码) / token(手动 Cookie)
const activeMethod = ref<'qr' | 'password' | 'token'>('qr')

const form = reactive({ account: '', password: '', cookie: '', userId: '', passToken: '' })

// 账密登录时是否清空已保存的 Cookie (默认保留, 让账密只作自动恢复兜底)
const clearCookie = ref(false)

// 扫码登录状态 (token 全程留在后端, 前端仅依据 state 展示)
const qr = reactive<{
  sessionId: string
  qrcodeUrl: string
  loginUrl: string
  state: QRPollState
  statusText: string
  starting: boolean
  error: string
}>({
  sessionId: '',
  qrcodeUrl: '',
  loginUrl: '',
  state: 'waiting',
  statusText: '请使用米家 App 扫描二维码',
  starting: false,
  error: '',
})
let qrStopped = false

async function startQr() {
  qr.starting = true
  qr.error = ''
  try {
    const res = await startQRCode()
    if (!res.success || !res.session_id) {
      qr.error = res.error || '获取二维码失败, 请稍后重试'
      return
    }
    qrStopped = false
    qr.sessionId = res.session_id
    qr.qrcodeUrl = res.qrcode_url || ''
    qr.loginUrl = res.login_url || ''
    qr.state = 'waiting'
    qr.statusText = '请使用米家 App 扫描二维码'
    pollLoop(qr.sessionId)
  } catch (e: any) {
    qr.error = e.response?.data?.detail || '获取二维码失败, 请稍后重试'
  } finally {
    qr.starting = false
  }
}

async function pollLoop(sessionId: string) {
  // 绑定到本次会话; 会话变更 (重新获取) 时旧循环自动退出
  while (!qrStopped && qr.sessionId === sessionId) {
    let res
    try {
      res = await pollQRCode(sessionId)
    } catch (e: any) {
      if (qrStopped || qr.sessionId !== sessionId) return
      // 网络抖动: 稍候重试, 不中断轮询
      await new Promise((r) => setTimeout(r, 2000))
      continue
    }
    if (qrStopped || qr.sessionId !== sessionId) return
    qr.state = res.state
    if (res.state === 'confirmed') {
      qr.statusText = '登录成功, 请在下方勾选音箱并保存'
      message.success('扫码登录成功')
      qr.sessionId = ''
      // 刷新脱敏 Cookie、账号状态与设备列表
      // (状态卡不刷新的话 has_account 要等 30s 轮询才变, 禁用态的按钮会多灰半分钟)
      await loadAccountStatus()
      await loadInit()
      await loadDevices()
      // 首次登录后若尚未选择设备, 自动勾选检测到的全部音箱, 提示保存
      if (selectedDids.value.length === 0 && devices.value.length > 0) {
        selectedDids.value = devices.value.map((d) => d.miotDID)
        message.info('已自动勾选检测到的音箱, 请点击“保存并应用”完成配置')
      }
      return
    }
    if (res.state === 'expired' || res.state === 'failed') {
      qr.statusText = res.message || '二维码已失效, 请重新获取'
      qr.sessionId = ''
      return
    }
    // waiting: 继续下一次长轮询
  }
}

function resetQr() {
  qrStopped = true
  qr.sessionId = ''
  qr.qrcodeUrl = ''
  qr.loginUrl = ''
  qr.error = ''
  startQr()
}

// 密码登录交互会话 (登录 → 验证码/短信验证 → 成功)
const pw = reactive({
  sessionId: '',
  state: 'idle' as 'idle' | 'need_captcha' | 'need_verify',
  captchaImage: '',
  captchaCode: '',
  verifyType: 'phone' as 'phone' | 'email',
  verifyCode: '',
  loading: false,
  error: '',
})

function resetPwSession() {
  pw.sessionId = ''
  pw.state = 'idle'
  pw.captchaImage = ''
  pw.captchaCode = ''
  pw.verifyCode = ''
}

async function startPasswordLogin() {
  if (!form.account || !form.password) {
    message.warning('请输入账号和密码')
    return
  }
  pw.loading = true
  pw.error = ''
  resetPwSession()
  try {
    handlePwResult(await passwordLogin(form.account, form.password))
  } catch (e: any) {
    pw.error = e.response?.data?.detail || '登录失败'
  } finally {
    pw.loading = false
  }
}

async function submitPwCaptcha() {
  if (!pw.captchaCode.trim()) {
    message.warning('请输入图中验证码')
    return
  }
  pw.loading = true
  pw.error = ''
  try {
    handlePwResult(await submitLoginCaptcha(pw.sessionId, pw.captchaCode.trim()))
  } catch (e: any) {
    pw.error = e.response?.data?.detail || '验证码提交失败'
  } finally {
    pw.loading = false
  }
}

async function submitPwVerify() {
  if (!pw.verifyCode.trim()) {
    message.warning('请输入验证码')
    return
  }
  pw.loading = true
  pw.error = ''
  try {
    handlePwResult(await submitLoginVerifyCode(pw.sessionId, pw.verifyCode.trim()))
  } catch (e: any) {
    pw.error = e.response?.data?.detail || '验证码提交失败'
  } finally {
    pw.loading = false
  }
}

function handlePwResult(res: PasswordLoginResult) {
  if (res.success && res.state === 'success') {
    resetPwSession()
    message.success('登录成功, 服务正在重启')
    loadAccountStatus()
    loadInit()
    loadDevices()
    return
  }
  if (res.success && res.state === 'need_captcha') {
    pw.sessionId = res.session_id || ''
    pw.state = 'need_captcha'
    pw.captchaImage = res.captcha_image || ''
    pw.captchaCode = ''
    return
  }
  if (res.success && res.state === 'need_verify') {
    pw.sessionId = res.session_id || ''
    pw.state = 'need_verify'
    pw.verifyType = res.verify_type || 'phone'
    pw.verifyCode = ''
    return
  }
  resetPwSession()
  pw.error = res.error || '登录失败'
}

// 手动 Token 立即验证
const tokenVerifying = ref(false)
const tokenError = ref('')

async function verifyManualToken() {
  const u = form.userId.trim()
  const p = form.passToken.trim()
  if (!u || !p) {
    message.warning('请填写 User ID 和 Pass Token')
    return
  }
  tokenVerifying.value = true
  tokenError.value = ''
  try {
    const res = await setManualToken(u, p)
    if (res.success) {
      message.success('令牌验证成功, 服务正在重启')
      await loadAccountStatus()
      await loadInit()
      await loadDevices()
    } else {
      tokenError.value = res.error || '验证失败'
    }
  } catch (e: any) {
    tokenError.value = e.response?.data?.detail || '验证失败'
  } finally {
    tokenVerifying.value = false
  }
}
const devices = ref<CloudDevice[]>([])
const selectedDids = ref<string[]>([])
const loadingDevices = ref(false)
const saving = ref(false)
const deviceError = ref('')

async function loadInit() {
  const s = await fetchSettings()
  // 账号出于安全不回显, 仅回显脱敏 Cookie 与已选设备
  form.account = ''
  form.cookie = s.cookie
  // 解析已保存的 Cookie 拆回 userId / passToken 两个字段 (脱敏占位符不回填, 避免误导)
  const MASK = '****'
  const u = (s.cookie.match(/userId=([^;\s]+)/) || [])[1] || ''
  const p = (s.cookie.match(/passToken=([^;\s]+)/) || [])[1] || ''
  form.userId = u.includes(MASK) ? '' : u
  form.passToken = p.includes(MASK) ? '' : p
  selectedDids.value = s.mi_did ? s.mi_did.split(',').filter(Boolean) : []
}

async function loadDevices() {
  loadingDevices.value = true
  deviceError.value = ''
  try {
    const res = await fetchCloudDevices()
    devices.value = res.devices || []
    if (res.error) deviceError.value = res.error
  } catch (e: any) {
    deviceError.value = e.response?.data?.detail || '获取设备列表失败'
  } finally {
    loadingDevices.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const payload: any = { mi_did: selectedDids.value.join(',') }
    if (activeMethod.value === 'password') {
      if (!form.account || !form.password) {
        message.warning('请输入账号和密码')
        return
      }
      payload.account = form.account
      payload.password = form.password
      // 默认保留已保存的 Cookie 作为首选凭证, 账密仅作自动恢复兜底;
      // 勾选后才清空 Cookie, 强制改用账密登录
      if (clearCookie.value) payload.cookie = ''
    } else if (activeMethod.value === 'token') {
      const u = form.userId.trim()
      const p = form.passToken.trim()
      if (!u || !p) {
        message.warning('请填写 User ID 和 Pass Token')
        return
      }
      payload.cookie = `userId=${u}; passToken=${p}`
    } else {
      // 扫码: 凭证已由扫码流程写入后端, 回写脱敏 cookie 由后端自动还原
      payload.cookie = form.cookie
    }
    await saveSettings(payload)
    message.success('已保存, 服务正在重启, 稍后刷新查看')
    await loadInit()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

let statusTimer: number | undefined

onMounted(async () => {
  await loadInit()
  // 已配置凭证才自动拉音箱列表, 避免空 cookie 下 401
  if (form.cookie) await loadDevices()
  loadAccountStatus()
  // 每 30s 轮询状态, 让「剩余有效期 / 健康度」实时可见, 避免过期静默失败
  statusTimer = window.setInterval(loadAccountStatus, 30000)
})
onUnmounted(() => {
  qrStopped = true
  if (statusTimer) clearInterval(statusTimer)
})
</script>
