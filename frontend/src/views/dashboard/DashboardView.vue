<template>
  <n-space vertical :size="16">
    <!-- 正在播放摘要: 打开总览第一眼想看的信息 -->
    <n-alert :type="nowPlaying ? 'success' : 'default'" :bordered="false">
      <template #icon> ♪ </template>
      <template v-if="nowPlaying">
        <b>{{ nowPlaying.title || (nowPlaying.source === 'airplay' ? '正在通过 AirPlay 播放' : '正在播放') }}</b>
        <template v-if="nowPlaying.artist"> — {{ nowPlaying.artist }}</template>
        <n-text depth="3">
          ｜{{ nowPlaying.speakerName }}
          {{ nowPlaying.source === 'airplay' ? `· AirPlay (${nowPlaying.client})` : '· DLNA' }}
        </n-text>
      </template>
      <template v-else>当前空闲, 没有正在播放的内容</template>
    </n-alert>

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
      <n-gi span="4 s:2 m:1">
        <n-card>
          <n-statistic label="CPU 占用" :value="cpuText">
            <template #suffix>
              <svg v-if="cpuHistory.length > 1" width="72" height="22" class="spark">
                <polyline :points="cpuSparkPoints" fill="none" stroke="#18a058" stroke-width="1.5" />
              </svg>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="磁盘剩余" :value="diskText" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="小米凭证" :value="tokenText" /></n-card>
      </n-gi>
      <n-gi span="4 s:2 m:1">
        <n-card><n-statistic label="运行环境" :value="runtimeText" /></n-card>
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
            <div v-if="sp.now_playing?.playing && sp.now_playing.title" class="uri">
              ♪ {{ sp.now_playing.title }}<template v-if="sp.now_playing.artist"> — {{ sp.now_playing.artist }}</template>
            </div>
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
  NAlert, NText,
} from 'naive-ui'
import { useWebSocket } from '@/composables/useWebSocket'
import { fetchStatus, type SystemStatus } from '@/api/system'

const { connected, status } = useWebSocket()

const playingCount = computed(
  () => status.value?.speakers.filter((s) => s.transport_state === 'PLAYING' || s.airplay_active).length ?? 0,
)

// 正在播放摘要 (取第一个播放中的音箱; AirPlay 优先, 后端已按实际发声路线填好)
const nowPlaying = computed(() => {
  for (const sp of status.value?.speakers ?? []) {
    const np = sp.now_playing
    if (np?.playing) {
      return { ...np, speakerName: sp.dlna_name }
    }
  }
  return null
})

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
  const s = sysStatus.value
  const m = s?.memory_mb
  if (m == null) return '-'
  // 口径标注: cgroup=容器内存(与 docker stats 一致), pss/rss=进程内存, peak=峰值
  const sourceLabel = s?.memory_source === 'cgroup' ? ' (容器)' : ''
  return `${m} MB${sourceLabel}`
})

// ---- CPU: 保留最近 30 个采样画 sparkline ----
const cpuHistory = ref<number[]>([])
const cpuText = computed(() => {
  const c = sysStatus.value?.cpu_percent
  return c == null ? '-' : `${c}%`
})
const cpuSparkPoints = computed(() => {
  const h = cpuHistory.value
  if (h.length < 2) return ''
  const max = Math.max(...h, 10)
  const w = 72
  const step = w / (h.length - 1)
  return h.map((v, i) => `${(i * step).toFixed(1)},${(20 - (v / max) * 18).toFixed(1)}`).join(' ')
})

const diskText = computed(() => {
  const d = sysStatus.value?.disk_free_gb
  return d == null ? '-' : `${d} GB`
})

const tokenText = computed(() => {
  const h = sysStatus.value?.service_token_remaining_hours
  if (h == null) return '未知'
  if (h < 0) return '已过期'
  if (h < 1) return `${Math.max(0, Math.round(h * 60))} 分钟`
  return `${h} 小时`
})

const runtimeText = computed(() => {
  const s = sysStatus.value
  if (!s?.python_version) return '-'
  return `py${s.python_version.split('.').slice(0, 2).join('.')}`
})

async function loadSysStatus() {
  try {
    sysStatus.value = await fetchStatus()
    const c = sysStatus.value?.cpu_percent
    if (c != null) {
      cpuHistory.value.push(c)
      if (cpuHistory.value.length > 30) cpuHistory.value.shift()
    }
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
.spark {
  margin-bottom: 10px;
  margin-left: 6px;
}
</style>
