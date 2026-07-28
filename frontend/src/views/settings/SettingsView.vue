<template>
  <n-space vertical :size="16">
    <n-spin :show="loading">
      <n-card title="播放行为">
        <n-form label-placement="left" label-width="160" :show-feedback="false">
          <n-space vertical :size="18">
            <n-form-item label="设置 URI 后自动播放">
              <n-switch v-model:value="form.auto_play_on_set_uri" />
            </n-form-item>
            <n-form-item label="被打断后自动恢复">
              <n-switch v-model:value="form.auto_resume_on_interrupt" />
            </n-form-item>
            <n-form-item label="恢复延迟 (秒)">
              <n-input-number v-model:value="form.resume_delay_seconds" :min="1" :max="15" />
            </n-form-item>
            <n-form-item label="默认音量">
              <n-input-number v-model:value="form.default_volume" :min="1" :max="100" />
            </n-form-item>
            <n-form-item label="跟随设备音量">
              <n-switch v-model:value="form.follow_device_volume" />
            </n-form-item>
          </n-space>
        </n-form>
      </n-card>

      <n-card title="服务与网络" style="margin-top: 16px">
        <n-form label-placement="left" label-width="160" :show-feedback="false">
          <n-space vertical :size="18">
            <n-form-item label="DLNA 端口">
              <n-input-number v-model:value="form.dlna_port" :min="1" :max="65535" />
            </n-form-item>
            <n-form-item label="设备离线自动重启">
              <n-switch v-model:value="form.auto_restart" />
            </n-form-item>
          </n-space>
        </n-form>
      </n-card>
    </n-spin>

    <n-space>
      <n-button type="primary" :loading="saving" @click="save">保存并应用 (热重启服务)</n-button>
      <n-popconfirm @positive-click="doRestartProcess">
        <template #trigger>
          <n-button>重启整个进程</n-button>
        </template>
        确认重启进程? Docker 环境下将由容器策略自动拉起。
      </n-popconfirm>
    </n-space>

    <n-card size="small" title="版本与更新">
      <template #header-extra>
        <n-button size="small" :loading="checking" @click="doCheckUpdate">检查更新</n-button>
      </template>
      <n-descriptions :column="2" label-placement="left" size="small">
        <n-descriptions-item label="应用版本">{{ info.version }}</n-descriptions-item>
        <n-descriptions-item label="协议引擎版本">{{ info.engine_version }}</n-descriptions-item>
        <n-descriptions-item label="主机名">{{ info.hostname }}</n-descriptions-item>
        <n-descriptions-item label="渲染器数量">{{ info.renderers_count }}</n-descriptions-item>
      </n-descriptions>

      <n-alert
        v-if="update.checked && update.info?.update_available"
        type="success"
        :bordered="false"
        style="margin-top: 12px"
        :title="`发现新版本 v${update.info.latest}`"
      >
        <div>当前 v{{ update.info.current }} → 最新 v{{ update.info.latest }}。选择一种方式升级:</div>
        <div v-for="cmd in upgradeCommands" :key="cmd.cmd" style="margin-top: 6px">
          <span style="margin-right: 8px">{{ cmd.label }}:</span>
          <n-text code>{{ cmd.cmd }}</n-text>
          <n-button size="tiny" quaternary style="margin-left: 6px" @click="copyCommand(cmd.cmd)">复制</n-button>
        </div>
        <div v-if="update.info.release_url" style="margin-top: 6px">
          <a :href="update.info.release_url" target="_blank" rel="noopener">查看发行说明</a>
        </div>
      </n-alert>
      <n-alert
        v-else-if="update.checked && update.info?.error"
        type="warning"
        :bordered="false"
        style="margin-top: 12px"
      >
        {{ update.info.error }}
      </n-alert>
      <n-alert
        v-else-if="update.checked"
        type="default"
        :bordered="false"
        style="margin-top: 12px"
      >
        已是最新版本。
      </n-alert>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  NSpace, NCard, NForm, NFormItem, NSwitch, NInputNumber, NButton, NSpin,
  NPopconfirm, NDescriptions, NDescriptionsItem, NAlert, NText, useMessage,
} from 'naive-ui'
import { fetchSettings, saveSettings, restartProcess, checkUpdate, type UpdateInfo } from '@/api/system'

const message = useMessage()
const loading = ref(false)
const saving = ref(false)

const form = reactive({
  auto_play_on_set_uri: true,
  auto_resume_on_interrupt: true,
  resume_delay_seconds: 3,
  default_volume: 50,
  follow_device_volume: false,
  dlna_port: 8300,
  auto_restart: true,
})

const info = reactive({
  version: '',
  engine_version: '',
  hostname: '',
  renderers_count: 0,
})

async function load() {
  loading.value = true
  try {
    const s = await fetchSettings()
    form.auto_play_on_set_uri = s.auto_play_on_set_uri
    form.auto_resume_on_interrupt = s.auto_resume_on_interrupt
    form.resume_delay_seconds = s.resume_delay_seconds
    form.default_volume = s.default_volume
    form.follow_device_volume = s.follow_device_volume
    form.dlna_port = s.dlna_port
    form.auto_restart = s.auto_restart
    info.version = s.version
    info.engine_version = s.engine_version
    info.hostname = s.hostname
    info.renderers_count = s.renderers_count
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await saveSettings({ ...form })
    message.success('已保存, 服务正在热重启')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doRestartProcess() {
  await restartProcess()
  message.info('进程正在重启, 稍后请刷新页面')
}

const checking = ref(false)
const update = reactive<{ checked: boolean; info: UpdateInfo | null }>({
  checked: false,
  info: null,
})

// 升级命令 (一键安装脚本部署 / 手动 docker 部署 两种场景)
const upgradeCommands = [
  { label: '脚本部署', cmd: './manage.sh update' },
  { label: '手动部署', cmd: 'docker pull mrdeer1997/miair-next:latest' },
]

async function copyCommand(cmd: string) {
  try {
    await navigator.clipboard.writeText(cmd)
    message.success('已复制')
  } catch {
    message.error('复制失败, 请手动选中复制')
  }
}

async function doCheckUpdate() {
  checking.value = true
  try {
    update.info = await checkUpdate()
    update.checked = true
  } catch (e: any) {
    message.error(e.response?.data?.detail || '检查更新失败')
  } finally {
    checking.value = false
  }
}

onMounted(load)
</script>
