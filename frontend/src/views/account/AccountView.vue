<template>
  <n-space vertical :size="16">
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
            <n-text depth="3" style="font-size: 12px">
              无法扫码?
              <a :href="qr.loginUrl" target="_blank" rel="noopener">点此在浏览器打开登录页</a>
            </n-text>
            <n-button size="small" @click="resetQr">重新获取</n-button>
          </n-space>
        </n-tab-pane>

        <!-- 方式二: 账号密码 -->
        <n-tab-pane name="password" tab="账号密码">
          <n-alert type="warning" :bordered="false" style="margin-bottom: 16px">
            账号密码登录可能触发滑块 / 短信验证码, 若登录失败请改用扫码登录。
          </n-alert>
          <n-form label-placement="top">
            <n-form-item label="账号 (手机号 / 邮箱 / 小米 ID)">
              <n-input v-model:value="form.account" placeholder="请输入账号" />
            </n-form-item>
            <n-form-item label="密码">
              <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="请输入密码" />
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- 方式三: 手动 Token -->
        <n-tab-pane name="token" tab="手动 Token">
          <n-alert type="info" :bordered="false" style="margin-bottom: 16px">
            适合高级用户: 手动填入从浏览器 / 抓包获取的 userId 与 passToken。
          </n-alert>
          <n-form label-placement="top">
            <n-form-item label="Cookie (userId + passToken)">
              <n-input v-model:value="form.cookie" type="textarea" :rows="3" placeholder="userId=xxx; passToken=xxx" />
            </n-form-item>
          </n-form>
        </n-tab-pane>
      </n-tabs>
    </n-card>

    <n-card title="选择音箱设备">
      <template #header-extra>
        <n-button size="small" :loading="loadingDevices" @click="loadDevices">获取设备列表</n-button>
      </template>
      <n-alert v-if="deviceError" type="warning" :bordered="false">{{ deviceError }}</n-alert>
      <n-checkbox-group v-model:value="selectedDids">
        <n-space vertical>
          <n-checkbox v-for="d in devices" :key="d.miotDID" :value="d.miotDID">
            {{ d.name }} <n-text depth="3">({{ d.hardware }})</n-text>
          </n-checkbox>
        </n-space>
      </n-checkbox-group>
      <n-empty v-if="devices.length === 0" description="点击右上角获取设备列表" />
    </n-card>

    <n-button type="primary" :loading="saving" @click="save">保存并应用 (将重启服务)</n-button>
  </n-space>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  NSpace, NCard, NForm, NFormItem, NInput, NButton, NAlert, NCheckbox,
  NCheckboxGroup, NText, NEmpty, NSpin, NTabs, NTabPane, useMessage,
} from 'naive-ui'
import { fetchSettings, saveSettings } from '@/api/system'
import { fetchCloudDevices, type CloudDevice } from '@/api/speakers'
import { startQRCode, pollQRCode, type QRPollState } from '@/api/account'

const message = useMessage()

// 当前登录方式: qr(扫码) / password(账号密码) / token(手动 Cookie)
const activeMethod = ref<'qr' | 'password' | 'token'>('qr')

const form = reactive({ account: '', password: '', cookie: '' })

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
      // 刷新脱敏 Cookie 与设备列表
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
      // 后端 cookie 优先于账号密码, 切换到密码登录需清空已存 cookie
      payload.cookie = ''
    } else if (activeMethod.value === 'token') {
      if (!form.cookie) {
        message.warning('请填写 Cookie (userId + passToken)')
        return
      }
      payload.cookie = form.cookie
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

onMounted(loadInit)
onUnmounted(() => {
  qrStopped = true
})
</script>
