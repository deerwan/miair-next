<template>
  <toolbar-card>
    <template #toolbar>
      <n-space :size="8" align="center">
        <n-tag :type="connected ? 'success' : 'error'" size="small" round>
          {{ connected ? '实时连接' : '已断开' }}
        </n-tag>
        <n-select
          v-model:value="logLevel"
          size="small"
          :options="levelOptions"
          :loading="levelLoading"
          style="width: 130px"
          @update:value="changeLevel"
        />
        <n-checkbox v-model:checked="autoScroll">自动滚动</n-checkbox>
        <n-button size="small" :loading="downloading" @click="download">下载完整日志</n-button>
        <n-popconfirm @positive-click="clear">
          <template #trigger>
            <n-button size="small">清空</n-button>
          </template>
          确定清空当前展示的日志吗?
        </n-popconfirm>
      </n-space>
    </template>

    <n-log
      ref="logRef"
      :log="logText"
      :rows="26"
      trim
      style="font-size: 12px"
    />
  </toolbar-card>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  NSpace, NTag, NButton, NCheckbox, NLog, NPopconfirm, NSelect, useMessage,
} from 'naive-ui'
import { fetchLogs, fetchLogLevel, setLogLevel, downloadLogs, type LogLevel } from '@/api/system'
import { useWebSocket } from '@/composables/useWebSocket'
import ToolbarCard from '@/components/ToolbarCard.vue'

const message = useMessage()
const { connected, logLines } = useWebSocket()
const history = ref<string[]>([])
const autoScroll = ref(true)
const logRef = ref<InstanceType<typeof NLog> | null>(null)

// 日志等级 (运行时切换后端 logger, 不持久化, 重启后恢复默认)
const logLevel = ref<LogLevel>('info')
const levelLoading = ref(false)
const downloading = ref(false)
const levelOptions = [
  { label: 'DEBUG (详细)', value: 'debug' },
  { label: 'INFO (默认)', value: 'info' },
  { label: 'WARNING (警告)', value: 'warning' },
  { label: 'ERROR (错误)', value: 'error' },
]

async function changeLevel(level: LogLevel) {
  levelLoading.value = true
  try {
    await setLogLevel(level)
    message.success(`日志等级已切换为 ${level.toUpperCase()}`)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换日志等级失败')
    // 切换失败时回读服务端实际等级
    try {
      logLevel.value = (await fetchLogLevel()).level
    } catch { /* 忽略 */ }
  } finally {
    levelLoading.value = false
  }
}

// 历史日志 (挂载时拉取) + 实时日志 (WebSocket) 合并展示
const logText = computed(() => [...history.value, ...logLines.value].join('\n'))

watch(logText, () => {
  if (autoScroll.value) {
    nextTick(() => logRef.value?.scrollTo({ position: 'bottom', silent: true }))
  }
})

function clear() {
  history.value = []
  logLines.value.length = 0
}

async function download() {
  downloading.value = true
  try {
    await downloadLogs()
  } catch (e: any) {
    message.error(e.response?.status === 404 ? '日志文件不存在' : '下载日志失败')
  } finally {
    downloading.value = false
  }
}

onMounted(async () => {
  try {
    const res = await fetchLogs(300)
    history.value = res.lines
  } catch {
    /* 忽略历史日志加载失败 */
  }
  try {
    logLevel.value = (await fetchLogLevel()).level
  } catch {
    /* 忽略等级读取失败, 保持默认展示 */
  }
})
</script>
