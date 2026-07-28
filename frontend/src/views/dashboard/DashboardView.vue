<template>
  <n-space vertical :size="16">
    <n-grid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="服务状态" :value="status?.dlna_running ? '运行中' : '未启动'" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="渲染器数量" :value="status?.renderers_count ?? 0" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="实时连接" :value="connected ? '已连接' : '断开'" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="正在播放" :value="playingCount" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="运行时长" :value="uptimeText" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="内存占用" :value="memoryText" /></n-card>
      </n-gi>
    </n-grid>

    <n-card title="音箱实时状态">
      <n-empty v-if="!status || status.speakers.length === 0" description="暂无音箱, 请先在账号配置中选择设备" />
      <n-list v-else>
        <n-list-item v-for="sp in status.speakers" :key="sp.did">
          <n-thing :title="sp.dlna_name">
            <template #description>
              <n-space :size="8">
                <n-tag :type="sp.transport_state === 'PLAYING' ? 'success' : 'default'" size="small">
                  DLNA: {{ sp.transport_state }}
                </n-tag>
                <n-tag :type="sp.airplay_active ? 'success' : 'default'" size="small">
                  AirPlay: {{ sp.airplay_active ? '播放中' : '空闲' }}
                </n-tag>
              </n-space>
            </template>
            <div v-if="sp.current_uri" class="uri">{{ sp.current_uri }}</div>
          </n-thing>
        </n-list-item>
      </n-list>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  NSpace, NGrid, NGi, NCard, NStatistic, NList, NListItem, NThing, NTag, NEmpty,
} from 'naive-ui'
import { useWebSocket } from '@/composables/useWebSocket'
import { fetchStatus, type SystemStatus } from '@/api/system'

const { connected, status } = useWebSocket()

const playingCount = computed(
  () => status.value?.speakers.filter((s) => s.transport_state === 'PLAYING' || s.airplay_active).length ?? 0,
)

// 运行时长 / 内存指标 (REST 拉取, 30s 刷新一次)
const sysStatus = ref<SystemStatus | null>(null)
let timer: number | undefined

const uptimeText = computed(() => {
  const s = sysStatus.value?.uptime_seconds
  if (s == null) return '-'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分`
  return `${Math.floor(s / 86400)} 天 ${Math.floor((s % 86400) / 3600)} 时`
})

const memoryText = computed(() => {
  const m = sysStatus.value?.memory_mb
  return m == null ? '-' : `${m} MB`
})

async function loadSysStatus() {
  try {
    sysStatus.value = await fetchStatus()
  } catch {
    // 总览指标非关键信息, 失败不打断页面
  }
}

onMounted(() => {
  loadSysStatus()
  timer = window.setInterval(loadSysStatus, 30000)
})
onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.uri {
  font-size: 12px;
  color: #999;
  word-break: break-all;
  margin-top: 4px;
}
</style>
